"""Specification tests of `worker/replies.py` (ADR-0019, points 2, 3, 7 and 9; ADR-0020, points
2, 3, 12 and 13): pure functions, no I/O."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from dzdia_newsroom.channels.base import InboundMessage
from dzdia_newsroom.guardrails.ssrf import BlockedUrlError
from dzdia_newsroom.llm.base import Usage
from dzdia_newsroom.tools.article import (
    ArticleDraft,
    ArticleResult,
    Attempt,
    GenerationFailedError,
    Paragraph,
    QuoteSegment,
    SentenceSegment,
    render_text,
)
from dzdia_newsroom.tools.dossier import (
    Added,
    DossierState,
    RefusalReason,
    Refused,
    Source,
    SourceKind,
    WrittenArticle,
    length_budget,
)
from dzdia_newsroom.tools.dossier_article import (
    DossierArticle,
    DossierArticleResult,
    HeadedParagraph,
    paragraph_sources,
    render_dossier_text,
)
from dzdia_newsroom.tools.draft import Draft
from dzdia_newsroom.tools.evidence import build_dossier_evidence, build_evidence
from dzdia_newsroom.tools.fetch_page import (
    FetchError,
    FetchReason,
    InvalidLinkError,
    UnsupportedLinkError,
)
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS, NoReadableTextError
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.replies import (
    Command,
    ReplyWarning,
    Route,
    RouteKind,
    added_line,
    article_message,
    cleared_text,
    find_links,
    format_chars,
    group_error_text,
    help_text,
    link_error_line,
    opening,
    parse_command,
    receipt,
    refused_line,
    removed_quotes_line,
    render_reply,
    route_message,
    source_line,
    sources_message,
    state_line,
    thin_warning,
    warnings_for,
    write_ack,
)

NNBSP = " "  # narrow no-break space: the thousands separator

# --- group texts (ADR-0019, point 9; ADR-0020, point 13) -------------------------------------


def test_the_group_texts() -> None:
    assert replies.ACK_LINK == "Lien reçu, je prépare un brouillon."
    assert replies.ACK_SEVERAL_LINKS == (
        "Lien reçu, je prépare un brouillon. Je ne traite que le premier lien du message."
    )
    assert replies.LINK_ERROR == (
        "Je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il s'ouvre dans un "
        "navigateur, puis renvoyez-le."
    )
    assert replies.UNSUPPORTED_LINK_ERROR == (
        "Je ne lis pas encore les vidéos ni les publications des réseaux sociaux. Collez-moi le "
        "texte ou la légende (au moins 300 caractères) et j'en fais un brouillon."
    )
    assert replies.SITE_ERROR == (
        "Le site ne m'a pas laissé lire cette page. Collez-moi le texte de l'article dans un "
        "message et j'en fais un brouillon."
    )
    assert replies.INSIDE_ERROR == (
        "Je n'ai pas réussi à écrire ce brouillon. Le problème vient de mon côté, pas de votre "
        "envoi. Réessayez dans quelques minutes."
    )
    warning_sign = chr(0x26A0) + " "
    assert (
        warning_sign + "Cette page semble réservée aux abonnés : le brouillon ne couvre que la "
        "partie visible."
    ) == replies.PAYWALL_WARNING
    assert (
        warning_sign + "Le texte est très long : le brouillon ne couvre que le début."
    ) == replies.TRUNCATED_WARNING


def test_no_text_states_a_duration() -> None:
    texts = [value for name, value in vars(replies).items() if name.isupper()]
    assert texts and all("minute)" not in str(text) for text in texts)


def test_the_source_line_shows_the_link_as_sent() -> None:
    assert source_line("HTTPS://Example.org/a?b=1") == "Source : HTTPS://Example.org/a?b=1"


def test_the_removed_quote_line_has_a_singular_and_a_plural() -> None:
    assert removed_quotes_line(1) == "1 citation retirée : non confirmée par la source."
    assert removed_quotes_line(2) == "2 citations retirées : non confirmées par la source."


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidLinkError("x"), replies.LINK_ERROR),
        (BlockedUrlError("x"), replies.LINK_ERROR),
        (FetchError(FetchReason.NOT_FOUND, 404), replies.LINK_ERROR),
        (FetchError(FetchReason.REFUSED, 403), replies.SITE_ERROR),
        (FetchError(FetchReason.UNREACHABLE), replies.SITE_ERROR),
        (NoReadableTextError("x"), replies.SITE_ERROR),
        (UnsupportedLinkError("x"), replies.UNSUPPORTED_LINK_ERROR),
        (GenerationFailedError("x", attempts=()), replies.INSIDE_ERROR),
        (RuntimeError("x"), None),
    ],
)
def test_the_group_error_texts(exc: Exception, expected: str | None) -> None:
    assert group_error_text(exc) == expected


# --- private texts (ADR-0020, point 13) ------------------------------------------------------


def test_numbers_of_characters_use_a_narrow_no_break_space() -> None:
    assert format_chars(950) == "950"
    assert format_chars(1_800) == f"1{NNBSP}800"
    assert format_chars(60_000) == f"60{NNBSP}000"
    assert format_chars(1_234_567) == f"1{NNBSP}234{NNBSP}567"


def test_the_state_line() -> None:
    assert state_line(DossierState()) == "Le dossier est vide."
    assert state_line(DossierState(count=1, total_chars=1_800)) == (
        f"Le dossier contient 1 source, 1{NNBSP}800 caractères."
    )
    assert state_line(DossierState(count=3, total_chars=9_000)) == (
        f"Le dossier contient 3 sources, 9{NNBSP}000 caractères."
    )


HELP_HEAD = (
    "Envoyez-moi vos sources une par une : le lien d'un article, ou son texte collé (au moins "
    "300 caractères). Chaque envoi s'ajoute au dossier.\n"
    "/ecris : j'écris l'article à partir de toutes les sources.\n"
    "/nouveau : je vide le dossier."
)


def test_the_help_text_ends_with_the_state_line() -> None:
    assert help_text(DossierState()) == f"{HELP_HEAD}\n\nLe dossier est vide."
    assert help_text(DossierState(count=2, total_chars=700)) == (
        f"{HELP_HEAD}\n\nLe dossier contient 2 sources, 700 caractères."
    )


def test_the_write_acknowledgment() -> None:
    assert write_ack(1) == "J'écris l'article à partir de 1 source."
    assert write_ack(4) == "J'écris l'article à partir de 4 sources."


def test_the_empty_dossier_text() -> None:
    assert replies.EMPTY_DOSSIER == (
        "Le dossier est vide. Envoyez d'abord un lien ou collez un texte (au moins 300 "
        "caractères), puis /ecris."
    )


def test_the_cleared_text() -> None:
    assert cleared_text(0) == "Le dossier est déjà vide."
    assert cleared_text(1) == "Dossier vidé : 1 source retirée."
    assert cleared_text(3) == "Dossier vidé : 3 sources retirées."


def test_the_thin_warning() -> None:
    assert thin_warning(1, 1_800) == (
        f"⚠ Matière mince (1 source, 1{NNBSP}800 caractères) : l'article est court. Ajoutez des "
        "sources puis renvoyez /ecris pour un article plus long."
    )
    assert thin_warning(2, 900).startswith("⚠ Matière mince (2 sources, 900 caractères) : ")


def test_the_inside_texts() -> None:
    assert replies.WRITE_INSIDE_ERROR == (
        "Je n'ai pas réussi à écrire cet article. Le problème vient de mon côté, pas de vos "
        "sources. Renvoyez /ecris dans quelques minutes."
    )
    assert replies.SOURCE_INSIDE_ERROR == (
        "Je n'ai pas réussi à ajouter cette source. Le problème vient de mon côté, pas de votre "
        "envoi. Renvoyez-la dans quelques minutes."
    )


# --- receipt (ADR-0020, point 4) -------------------------------------------------------------


def added(
    position: int = 1,
    *,
    host: str | None = "example.org",
    chars: int = 1_800,
    truncated: bool = False,
    paywall: bool = False,
) -> Added:
    return Added(
        position=position,
        kind=SourceKind.LINK if host else SourceKind.PASTED_TEXT,
        host=host,
        stored_chars=chars,
        truncated=truncated,
        paywall_suspected=paywall,
        source_count=position,
        total_chars=chars,
    )


def test_an_added_link_and_an_added_text() -> None:
    assert added_line(added(2)) == f"Source 2 ajoutée : example.org, 1{NNBSP}800 caractères."
    assert added_line(added(1, host=None, chars=450)) == (
        "Source 1 ajoutée : texte collé, 450 caractères."
    )


def test_the_flags_of_an_added_source_follow_on_the_same_line() -> None:
    line = added_line(added(3, truncated=True, paywall=True))
    assert line == (
        f"Source 3 ajoutée : example.org, 1{NNBSP}800 caractères. Page peut-être réservée aux "
        "abonnés : seule la partie visible est gardée. Texte trop long : seul le début est "
        "gardé."
    )
    assert added_line(added(1, host=None, truncated=True)).endswith(
        "caractères. Texte trop long : seul le début est gardé."
    )


def test_refused_lines() -> None:
    already = Refused(RefusalReason.ALREADY_THERE)
    full = Refused(RefusalReason.FULL)
    assert refused_line(already, host="a.dz", pasted=False) == (
        "a.dz : ce lien est déjà dans le dossier."
    )
    assert refused_line(full, host="a.dz", pasted=False) == (
        "a.dz : non ajouté, le dossier est plein."
    )
    assert refused_line(full, host=None, pasted=True) == "Texte non ajouté : le dossier est plein."
    assert refused_line(full, host=None, pasted=False) == (
        "Ce lien : non ajouté, le dossier est plein."
    )


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            InvalidLinkError("x"),
            "a.dz : je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il s'ouvre "
            "dans un navigateur, puis renvoyez-le.",
        ),
        (BlockedUrlError("x"), "a.dz : je n'ai pas pu ouvrir ce lien."),
        (FetchError(FetchReason.NOT_FOUND, 404), "a.dz : je n'ai pas pu ouvrir ce lien."),
        (
            UnsupportedLinkError("x"),
            "a.dz : je ne lis pas encore les vidéos ni les publications des réseaux sociaux. "
            "Collez-moi le texte ou la légende (au moins 300 caractères) et je l'ajoute au "
            "dossier.",
        ),
        (
            FetchError(FetchReason.REFUSED, 403),
            "a.dz : le site ne m'a pas laissé lire cette page. Collez-moi le texte de l'article "
            "dans un message et je l'ajoute au dossier.",
        ),
        (NoReadableTextError("x"), "a.dz : le site ne m'a pas laissé lire cette page."),
    ],
)
def test_link_error_lines(exc: Exception, expected: str) -> None:
    assert link_error_line(exc, "a.dz").startswith(expected)


def test_a_link_error_line_without_a_host() -> None:
    assert link_error_line(InvalidLinkError("x"), None).startswith(
        "Ce lien : je n'ai pas pu ouvrir ce lien."
    )


def test_link_error_line_refuses_an_unknown_error() -> None:
    with pytest.raises(ValueError):
        link_error_line(RuntimeError("x"), "a.dz")


def test_the_receipt_lists_its_lines_then_the_state() -> None:
    lines = [added_line(added(1)), "b.dz : ce lien est déjà dans le dossier."]
    state = DossierState(count=1, total_chars=1_800)
    assert receipt(lines, state) == (
        f"Source 1 ajoutée : example.org, 1{NNBSP}800 caractères.\n"
        "b.dz : ce lien est déjà dans le dossier.\n\n"
        f"Le dossier contient 1 source, 1{NNBSP}800 caractères.\n"
        "/ecris pour écrire l'article, /nouveau pour repartir de zéro."
    )


def test_the_receipt_of_an_empty_dossier_has_no_next_step() -> None:
    lines = ["a.dz : je n'ai pas pu ouvrir ce lien."]
    assert receipt(lines, DossierState()) == (
        "a.dz : je n'ai pas pu ouvrir ce lien.\n\nLe dossier est vide."
    )


# --- links (ADR-0019, point 2) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Regarde https://example.org/a.", ["https://example.org/a"]),
        ("(voir https://example.org/a)", ["https://example.org/a"]),
        ("« https://example.org/a »", ["https://example.org/a"]),
        ("lien : https://example.org/a?x=1!?", ["https://example.org/a?x=1"]),
        ('"https://example.org/a",', ["https://example.org/a"]),
        ("https://example.org/a…", ["https://example.org/a"]),
        ("[https://example.org/a];", ["https://example.org/a"]),
        ("{https://example.org/a}':", ["https://example.org/a"]),
        (
            "https://fr.wikipedia.org/wiki/Babylone_(groupe)",
            ["https://fr.wikipedia.org/wiki/Babylone_(groupe)"],
        ),
        (
            "https://fr.wikipedia.org/wiki/Babylone_(groupe).",
            ["https://fr.wikipedia.org/wiki/Babylone_(groupe)"],
        ),
        (
            "(https://fr.wikipedia.org/wiki/Babylone_(groupe))",
            ["https://fr.wikipedia.org/wiki/Babylone_(groupe)"],
        ),
        ("https://example.org/a).", ["https://example.org/a"]),
        ("https://example.org/a_(b),", ["https://example.org/a_(b)"]),
        ("HTTPS://Example.org/A", ["HTTPS://Example.org/A"]),
        ("Http://example.org", ["Http://example.org"]),
        ("http://a.dz/1 puis https://b.dz/2.", ["http://a.dz/1", "https://b.dz/2"]),
        ("www.example.org/article", []),
        ("example.org/article", []),
        ("ftp://example.org/file", []),
        ("pas de lien ici", []),
    ],
)
def test_find_links(text: str, expected: list[str]) -> None:
    assert find_links(text) == expected


def test_a_no_break_space_ends_a_link() -> None:
    assert find_links(f"https://example.org/a{chr(0x00A0)}suite") == ["https://example.org/a"]


# --- commands (ADR-0020, point 3) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ecris", Command.WRITE),
        ("/écris", Command.WRITE),
        ("/écris", Command.WRITE),  # decomposed accent: NFC first
        ("  /ECRIS  ", Command.WRITE),
        ("/Écris", Command.WRITE),
        ("/ecris@DzdiaBot", Command.WRITE),
        ("/ecris un article sur le festival", Command.WRITE),
        ("/ecris\nhttps://example.org/a", Command.WRITE),
        ("/nouveau", Command.NEW),
        ("/NOUVEAU@dzdia_bot merci", Command.NEW),
        ("/start", Command.HELP),
        ("/start@DzdiaBot", Command.HELP),
        ("/aide", Command.HELP),
        ("/", Command.HELP),
        ("/ecrire", Command.HELP),
        ("ecris", Command.WRITE),
        ("écris", Command.WRITE),
        (" Écris \n", Command.WRITE),
        ("nouveau", Command.NEW),
        ("Nouveau", Command.NEW),
    ],
)
def test_commands(text: str, expected: Command) -> None:
    assert parse_command(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Bonjour",
        "ecris l'article",
        "peux-tu écris",
        "nouveau dossier",
        "un nouveau",
        "écris.",
        "https://example.org/ecris",
        "texte /ecris",
    ],
)
def test_not_commands(text: str) -> None:
    assert parse_command(text) is None


# --- routing (ADR-0019, point 3; ADR-0020, point 2) ------------------------------------------


def message(text: str, *, private: bool = True) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        event_id="1",
        chat_id="111" if private else "-100123",
        user_id="111",
        message_id="42",
        is_private=private,
        text=text,
        sent_at=datetime(2026, 11, 15, tzinfo=UTC),
        raw={},
    )


LONG_TEXT = "a" * MIN_SOURCE_CHARS


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("/ecris", RouteKind.WRITE),
        ("écris", RouteKind.WRITE),
        ("/nouveau", RouteKind.NEW),
        ("nouveau", RouteKind.NEW),
        ("/start", RouteKind.HELP),
        ("/inconnu", RouteKind.HELP),
        # a command wins over the links and the length of the text that follows it
        ("/ecris https://example.org/a", RouteKind.WRITE),
        (f"/nouveau {LONG_TEXT}", RouteKind.NEW),
    ],
)
def test_private_commands_come_first(text: str, kind: RouteKind) -> None:
    assert route_message(message(text)) == Route(kind)


def test_private_links_become_sources_in_order() -> None:
    route = route_message(message("Lis https://a.dz/1 puis https://b.dz/2."))
    assert route == Route(RouteKind.SOURCE, links=("https://a.dz/1", "https://b.dz/2"))


def test_private_duplicate_links_are_kept_once() -> None:
    route = route_message(message("https://a.dz/1 https://b.dz/2 https://a.dz/1"))
    assert route == Route(RouteKind.SOURCE, links=("https://a.dz/1", "https://b.dz/2"))


def test_a_long_text_wins_over_its_links() -> None:
    route = route_message(message(f"https://a.dz/1 {LONG_TEXT} https://example.org/a"))
    assert route == Route(RouteKind.SOURCE)  # a pasted text: no link is fetched


def test_links_do_not_count_in_the_length_of_a_pasted_text() -> None:
    short = "a" * (MIN_SOURCE_CHARS - 1)
    route = route_message(message(f"{short} https://example.org/{'b' * MIN_SOURCE_CHARS}"))
    assert route == Route(
        RouteKind.SOURCE, links=(f"https://example.org/{'b' * MIN_SOURCE_CHARS}",)
    )


def test_a_long_text_with_a_link_stays_a_link_in_the_team_group() -> None:
    route = route_message(message(f"{LONG_TEXT} https://example.org/a", private=False))
    assert route == Route(RouteKind.LINK, link="https://example.org/a")


def test_a_long_text_without_link_is_a_pasted_source() -> None:
    assert route_message(message(LONG_TEXT)) == Route(RouteKind.SOURCE)


def test_a_long_text_that_holds_a_bare_word_is_a_source() -> None:
    text = "écris " + LONG_TEXT
    assert route_message(message(text)) == Route(RouteKind.SOURCE)


def test_private_text_is_measured_stripped() -> None:
    padded = "  \n" + "a" * (MIN_SOURCE_CHARS - 1) + "\n   "
    assert route_message(message(padded)) == Route(RouteKind.HELP)


def test_private_short_text_gets_the_help() -> None:
    assert route_message(message("Bonjour")) == Route(RouteKind.HELP)


def test_private_link_without_scheme_is_not_a_link() -> None:
    assert route_message(message("www.example.org/article")) == Route(RouteKind.HELP)


def test_group_link_starts_the_link_path() -> None:
    route = route_message(message("https://example.org/a", private=False))
    assert route == Route(RouteKind.LINK, link="https://example.org/a", several_links=False)


def test_group_several_links_keep_the_first_and_say_so() -> None:
    route = route_message(message("https://a.dz/1 https://b.dz/2", private=False))
    assert route == Route(RouteKind.LINK, link="https://a.dz/1", several_links=True)


@pytest.mark.parametrize(
    "text", ["Bonjour", LONG_TEXT, "www.example.org/article", "/ecris", "écris", "/start"]
)
def test_group_message_without_link_is_silent(text: str) -> None:
    assert route_message(message(text, private=False)) == Route(RouteKind.SILENT)


def test_group_command_with_a_link_is_a_link() -> None:
    route = route_message(message("/ecris https://a.dz/1", private=False))
    assert route == Route(RouteKind.LINK, link="https://a.dz/1", several_links=False)


def test_the_route_values_are_the_log_values() -> None:
    assert {kind.value for kind in RouteKind} == {
        "link",
        "silent",
        "source",
        "write",
        "new",
        "help",
    }


# --- group reply (ADR-0019, point 7) ---------------------------------------------------------

EVIDENCE = build_evidence(
    "Le festival s'est ouvert le 3 mai au théâtre régional.\n"
    "« Nous reviendrons l'an prochain », a déclaré Karim Ziani."
)
ARTICLE = ArticleDraft(
    title="Festival d'Oran",
    paragraphs=[
        Paragraph(
            segments=[
                SentenceSegment(
                    kind="sentence", text="Le festival s'est ouvert le 3 mai.", evidence_ids=["E1"]
                ),
                QuoteSegment(
                    kind="quote",
                    speaker="Karim Ziani",
                    text="Nous reviendrons l'an prochain",
                    evidence_id="E2",
                ),
            ]
        )
    ],
)
BODY = render_text(ARTICLE, EVIDENCE)
LINK = "https://example.org/festival?utm=x"


def make_draft(
    *,
    removed_quotes: int = 0,
    source_chars: int = 500,
    paywall_suspected: bool = False,
    truncated: bool = False,
) -> Draft:
    attempt = Attempt(draft=ARTICLE, issues=(), usage=Usage(), latency_ms=1)
    result = ArticleResult(
        article=ARTICLE, attempts=(attempt,), removed_quotes=removed_quotes, model="fake-llm"
    )
    return Draft(
        result=result,
        evidence=EVIDENCE,
        source_chars=source_chars,
        paywall_suspected=paywall_suspected,
        truncated=truncated,
    )


def test_a_plain_reply_is_the_article_then_the_source_with_no_leading_blank_line() -> None:
    assert render_reply(make_draft(), link=LINK) == f"{BODY}\n\nSource : {LINK}"


def test_the_article_has_no_evidence_marker() -> None:
    reply = render_reply(make_draft(), link=LINK)
    assert "[1]" not in reply and "E1" not in reply


def test_the_removed_quote_line_comes_last_on_its_own_line() -> None:
    reply = render_reply(make_draft(removed_quotes=1), link=LINK)
    assert reply == (
        f"{BODY}\n\nSource : {LINK}\n1 citation retirée : non confirmée par la source."
    )


def test_the_paywall_warning_comes_first() -> None:
    reply = render_reply(make_draft(paywall_suspected=True), link=LINK)
    assert reply == f"{replies.PAYWALL_WARNING}\n\n{BODY}\n\nSource : {LINK}"


def test_the_truncation_warning() -> None:
    reply = render_reply(make_draft(truncated=True), link=LINK)
    assert reply == f"{replies.TRUNCATED_WARNING}\n\n{BODY}\n\nSource : {LINK}"


def test_the_warnings_are_in_order_one_per_line() -> None:
    draft = make_draft(paywall_suspected=True, truncated=True, removed_quotes=2)
    assert warnings_for(draft) == [ReplyWarning.PAYWALL, ReplyWarning.TRUNCATED]
    assert render_reply(draft, link=LINK) == (
        f"{replies.PAYWALL_WARNING}\n{replies.TRUNCATED_WARNING}\n\n{BODY}\n\nSource : {LINK}\n"
        "2 citations retirées : non confirmées par la source."
    )


# --- the two messages of /ecris (ADR-0020, point 12) -----------------------------------------


def opening_is(text: str, expected: str) -> None:
    assert opening(text) == expected


def test_a_short_opening_is_whole_with_whitespace_collapsed() -> None:
    opening_is("  Un   texte\ncourt.  ", "Un texte court.")
    exactly = "a" * 40
    opening_is(exactly, exactly)


def test_a_long_opening_is_cut_at_the_last_space_within_40_characters() -> None:
    text = "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre."
    opening_is(text, "Le festival du film d'Oran s'est ouvert…")
    # a space exactly at the 41st character still counts: 40 characters kept
    opening_is("a" * 40 + " suite", "a" * 40 + "…")


def test_an_opening_without_a_space_is_cut_at_40_characters() -> None:
    opening_is("x" * 60, "x" * 40 + "…")


LINK_TEXT = "Premier texte du festival.\n" + "a" * 3_000  # not thin on its own
PASTED_TEXT = "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre régional.\n" + "b" * 500
THIRD_TEXT = "Troisième source sur la clôture du festival.\n" + "c" * 700
SOURCES = (
    Source(
        position=1,
        kind=SourceKind.LINK,
        url="https://www.example.org/festival?utm=x",
        text=LINK_TEXT,
        source_chars=len(LINK_TEXT),
        truncated=False,
        paywall_suspected=False,
    ),
    Source(
        position=2,
        kind=SourceKind.PASTED_TEXT,
        url=None,
        text=PASTED_TEXT,
        source_chars=len(PASTED_TEXT),
        truncated=False,
        paywall_suspected=False,
    ),
    Source(
        position=3,
        kind=SourceKind.LINK,
        url="https://b.dz/cloture",
        text=THIRD_TEXT,
        source_chars=len(THIRD_TEXT) + 5_000,
        truncated=True,
        paywall_suspected=True,
    ),
)
DOSSIER_EVIDENCE = build_dossier_evidence([source.text for source in SOURCES])
# E1, E2: source 1; E3, E4: source 2; E5, E6: source 3.


def sentence(text: str, *ids: str) -> SentenceSegment:
    return SentenceSegment(kind="sentence", text=text, evidence_ids=list(ids))


DOSSIER_ARTICLE = DossierArticle(
    title="Le festival d'Oran",
    paragraphs=[
        HeadedParagraph(
            heading="",
            segments=[sentence("Le festival du film d'Oran a ouvert ses portes le 3 mai.", "E1")],
        ),
        HeadedParagraph(
            heading="La clôture",
            segments=[sentence("La clôture est prévue.", "E5", "E1")],
        ),
    ],
)


def written(
    article: DossierArticle = DOSSIER_ARTICLE,
    *,
    sources: tuple[Source, ...] = SOURCES,
    removed_quotes: int = 0,
) -> WrittenArticle:
    evidence = build_dossier_evidence([source.text for source in sources])
    return WrittenArticle(
        result=DossierArticleResult(
            article=article, attempts=(), removed_quotes=removed_quotes, model="fake-llm"
        ),
        evidence=evidence,
        sources=sources,
        budget=length_budget([len(source.text) for source in sources]),
        paragraph_sources=paragraph_sources(article, evidence),
    )


def test_the_article_message_without_the_thin_warning() -> None:
    result = written()
    assert result.budget.thin is False
    assert article_message(result) == render_dossier_text(DOSSIER_ARTICLE, DOSSIER_EVIDENCE)
    assert "La clôture\n\nLa clôture est prévue." in article_message(result)


def test_the_article_message_of_thin_material_starts_with_the_warning() -> None:
    article = DossierArticle(
        title="Festival",
        paragraphs=[HeadedParagraph(heading="", segments=[sentence("Ouverture.", "E1")])],
    )
    result = written(article, sources=SOURCES[1:2])
    assert result.budget.thin is True
    chars = len(PASTED_TEXT)
    assert article_message(result) == (
        f"{thin_warning(1, chars)}\n\n{render_dossier_text(article, result.evidence)}"
    )


def test_the_sources_message() -> None:
    assert sources_message(written(removed_quotes=2)) == (
        "Sources\n"
        "1. www.example.org\n"
        "https://www.example.org/festival?utm=x\n"
        "2. Texte collé : Le festival du film d'Oran s'est ouvert…\n"
        "3. b.dz (page peut-être réservée aux abonnés, texte coupé)\n"
        "https://b.dz/cloture\n"
        "\n"
        "Paragraphes\n"
        "1. Le festival du film d'Oran a ouvert ses… → 1\n"
        "2. La clôture est prévue. → 1, 3\n"
        "\n"
        "Source non utilisée : 2\n"
        "2 citations retirées : non confirmées par la source."
    )


def test_the_flags_of_a_source_appear_alone_too() -> None:
    sources = (
        replace(SOURCES[0], truncated=True),
        replace(SOURCES[1], truncated=True),
        replace(SOURCES[2], truncated=False),
    )
    lines = sources_message(written(sources=sources)).splitlines()
    assert lines[1] == "1. www.example.org (texte coupé)"
    assert lines[3] == "2. Texte collé : Le festival du film d'Oran s'est ouvert… (texte coupé)"
    assert lines[4] == "3. b.dz (page peut-être réservée aux abonnés)"


def test_several_unused_sources_and_no_removed_quote() -> None:
    article = DossierArticle(
        title="Festival",
        paragraphs=[HeadedParagraph(heading="", segments=[sentence("Clôture.", "E5")])],
    )
    message = sources_message(written(article))
    assert message.endswith("Paragraphes\n1. Clôture. → 3\n\nSources non utilisées : 1, 2")


def test_the_last_block_is_omitted_when_every_source_is_used_and_no_quote_removed() -> None:
    article = DossierArticle(
        title="Festival",
        paragraphs=[
            HeadedParagraph(heading="Partie", segments=[sentence("Tout.", "E1", "E3", "E5")])
        ],
    )
    message = sources_message(written(article))
    assert message.endswith("Paragraphes\n1. Tout. → 1, 2, 3")


def test_only_the_removed_quote_line_in_the_last_block() -> None:
    article = DossierArticle(
        title="Festival",
        paragraphs=[HeadedParagraph(heading="", segments=[sentence("Tout.", "E1", "E3", "E5")])],
    )
    message = sources_message(written(article, removed_quotes=1))
    assert message.endswith(
        "1. Tout. → 1, 2, 3\n\n1 citation retirée : non confirmée par la source."
    )


def test_a_quote_paragraph_opening_is_its_rendered_text() -> None:
    evidence_text = "« Nous reviendrons l'an prochain », a déclaré Karim Ziani."
    sources = (replace(SOURCES[1], text=evidence_text + "\n" + "d" * 400),)
    quote = QuoteSegment(
        kind="quote",
        speaker="Karim Ziani",
        text="Nous reviendrons l'an prochain",
        evidence_id="E1",
    )
    article = DossierArticle(
        title="Festival", paragraphs=[HeadedParagraph(heading="", segments=[quote])]
    )
    lines = sources_message(written(article, sources=sources)).splitlines()
    assert "1. Karim Ziani : « Nous reviendrons l'an… → 1" in lines
