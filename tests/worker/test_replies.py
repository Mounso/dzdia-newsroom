"""Specification tests of `worker/replies.py` (ADR-0019, points 2, 3, 7 and 9): pure functions,
no I/O."""

from datetime import UTC, datetime

import pytest

from dzdia_newsroom.channels.base import InboundMessage
from dzdia_newsroom.llm.base import Usage
from dzdia_newsroom.tools.article import (
    ArticleDraft,
    ArticleResult,
    Attempt,
    Paragraph,
    QuoteSegment,
    SentenceSegment,
    render_text,
)
from dzdia_newsroom.tools.draft import Draft
from dzdia_newsroom.tools.evidence import build_evidence
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.replies import (
    PASTED_TEXT_WARNING_CHARS,
    ReplyWarning,
    Route,
    RouteKind,
    find_links,
    removed_quotes_line,
    render_reply,
    route_message,
    source_line,
    warnings_for,
)

# --- texts (ADR-0019, point 9) ---------------------------------------------------------------


def test_the_texts_are_the_ones_of_the_adr_table() -> None:
    assert replies.ACK_LINK == "Lien reçu, je prépare un brouillon (environ une minute)."
    assert replies.ACK_SEVERAL_LINKS == (
        "Lien reçu, je prépare un brouillon (environ une minute). "
        "Je ne traite que le premier lien du message."
    )
    assert replies.ACK_PASTED_TEXT == "Texte reçu, je prépare un brouillon (environ une minute)."
    assert replies.HELP_TEXT == (
        "Envoyez-moi le lien d'un article, ou collez son texte (au moins 300 caractères) : "
        "je vous renvoie un brouillon avec sa source."
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
        warning_sign + "Ce texte est peut-être coupé par Telegram : le brouillon ne couvre que "
        "ce message."
    ) == replies.LONG_PASTE_WARNING
    assert (
        warning_sign + "Le texte est très long : le brouillon ne couvre que le début."
    ) == replies.TRUNCATED_WARNING


def test_the_source_line_shows_the_link_as_sent_or_the_pasted_text_label() -> None:
    assert source_line("HTTPS://Example.org/a?b=1") == "Source : HTTPS://Example.org/a?b=1"
    assert source_line(None) == "Source : texte collé"


def test_the_removed_quote_line_has_a_singular_and_a_plural() -> None:
    assert removed_quotes_line(1) == "1 citation retirée : non confirmée par la source."
    assert removed_quotes_line(2) == "2 citations retirées : non confirmées par la source."
    assert removed_quotes_line(5) == "5 citations retirées : non confirmées par la source."


# --- find_links (ADR-0019, point 2) ----------------------------------------------------------


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


# --- routing (ADR-0019, point 3) -------------------------------------------------------------


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


def test_private_link_starts_the_link_path() -> None:
    assert route_message(message("Lis ça https://example.org/a.")) == Route(
        RouteKind.LINK, link="https://example.org/a", several_links=False
    )


def test_private_several_links_keep_the_first_and_say_so() -> None:
    route = route_message(message("https://a.dz/1 https://b.dz/2"))
    assert route == Route(RouteKind.LINK, link="https://a.dz/1", several_links=True)


def test_a_link_wins_over_a_long_text() -> None:
    route = route_message(message(f"{LONG_TEXT} https://example.org/a"))
    assert route == Route(RouteKind.LINK, link="https://example.org/a", several_links=False)


def test_private_long_text_without_link_is_pasted_text() -> None:
    assert route_message(message(LONG_TEXT)) == Route(RouteKind.PASTED_TEXT)


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


@pytest.mark.parametrize("text", ["Bonjour", LONG_TEXT, "www.example.org/article"])
def test_group_message_without_link_is_silent(text: str) -> None:
    assert route_message(message(text, private=False)) == Route(RouteKind.SILENT)


# --- rendering (ADR-0019, point 7) -----------------------------------------------------------

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


def test_a_pasted_text_is_named_as_such() -> None:
    assert render_reply(make_draft(), link=None) == f"{BODY}\n\nSource : texte collé"


def test_the_removed_quote_line_comes_last_on_its_own_line() -> None:
    reply = render_reply(make_draft(removed_quotes=1), link=LINK)
    assert reply == (
        f"{BODY}\n\nSource : {LINK}\n1 citation retirée : non confirmée par la source."
    )
    plural = render_reply(make_draft(removed_quotes=3), link=None)
    assert plural.endswith(
        "Source : texte collé\n3 citations retirées : non confirmées par la source."
    )


def test_the_paywall_warning_comes_first() -> None:
    reply = render_reply(make_draft(paywall_suspected=True), link=LINK)
    assert reply == f"{replies.PAYWALL_WARNING}\n\n{BODY}\n\nSource : {LINK}"


def test_the_truncation_warning() -> None:
    reply = render_reply(make_draft(truncated=True), link=LINK)
    assert reply == f"{replies.TRUNCATED_WARNING}\n\n{BODY}\n\nSource : {LINK}"


def test_the_long_paste_warning_starts_at_4000_characters_of_pasted_text() -> None:
    assert PASTED_TEXT_WARNING_CHARS == 4000
    below = render_reply(make_draft(source_chars=3999), link=None)
    assert replies.LONG_PASTE_WARNING not in below
    at = render_reply(make_draft(source_chars=4000), link=None)
    assert at == f"{replies.LONG_PASTE_WARNING}\n\n{BODY}\n\nSource : texte collé"


def test_the_long_paste_warning_is_never_given_for_a_link() -> None:
    assert replies.LONG_PASTE_WARNING not in render_reply(make_draft(source_chars=9000), link=LINK)


def test_the_warnings_are_in_order_one_per_line() -> None:
    draft = make_draft(
        source_chars=31_000, paywall_suspected=True, truncated=True, removed_quotes=2
    )
    assert warnings_for(draft, pasted=True) == [
        ReplyWarning.PAYWALL,
        ReplyWarning.LONG_PASTE,
        ReplyWarning.TRUNCATED,
    ]
    reply = render_reply(draft, link=None)
    assert reply == (
        f"{replies.PAYWALL_WARNING}\n{replies.LONG_PASTE_WARNING}\n{replies.TRUNCATED_WARNING}"
        f"\n\n{BODY}\n\nSource : texte collé\n"
        "2 citations retirées : non confirmées par la source."
    )
