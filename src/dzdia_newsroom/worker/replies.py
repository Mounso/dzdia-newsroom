"""What the bot says and when (ADR-0019, points 2, 3, 7 and 9; ADR-0020, points 2, 3, 4, 12 and
13): the French texts, the link search, the commands, the routing rule and the rendering of the
replies. Pure functions, no I/O.

Changing a wording needs no ADR.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import httpx

from dzdia_newsroom.channels.base import InboundMessage
from dzdia_newsroom.guardrails.ssrf import BlockedUrlError
from dzdia_newsroom.tools.article import GenerationFailedError, render_paragraph, render_text
from dzdia_newsroom.tools.dossier import (
    Added,
    DossierState,
    RefusalReason,
    Refused,
    Source,
    SourceKind,
    WrittenArticle,
)
from dzdia_newsroom.tools.dossier_article import render_dossier_text
from dzdia_newsroom.tools.draft import Draft
from dzdia_newsroom.tools.fetch_page import (
    FetchError,
    FetchReason,
    InvalidLinkError,
    UnsupportedLinkError,
)
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS, NoReadableTextError

# --- team group texts (ADR-0019, point 9; no duration: ADR-0020, point 13) -------------------

ACK_LINK = "Lien reçu, je prépare un brouillon."
ACK_SEVERAL_LINKS = (
    "Lien reçu, je prépare un brouillon. Je ne traite que le premier lien du message."
)
LINK_ERROR = (
    "Je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il s'ouvre dans un "
    "navigateur, puis renvoyez-le."
)
UNSUPPORTED_LINK_ERROR = (
    "Je ne lis pas encore les vidéos ni les publications des réseaux sociaux. Collez-moi le "
    "texte ou la légende (au moins 300 caractères) et j'en fais un brouillon."
)
SITE_ERROR = (
    "Le site ne m'a pas laissé lire cette page. Collez-moi le texte de l'article dans un "
    "message et j'en fais un brouillon."
)
INSIDE_ERROR = (
    "Je n'ai pas réussi à écrire ce brouillon. Le problème vient de mon côté, pas de votre "
    "envoi. Réessayez dans quelques minutes."
)
_WARNING_SIGN = "⚠ "
PAYWALL_WARNING = (
    _WARNING_SIGN
    + "Cette page semble réservée aux abonnés : le brouillon ne couvre que la partie visible."
)
TRUNCATED_WARNING = _WARNING_SIGN + "Le texte est très long : le brouillon ne couvre que le début."

# --- private chat texts (ADR-0020, point 13) -------------------------------------------------

EMPTY_DOSSIER = (
    "Le dossier est vide. Envoyez d'abord un lien ou collez un texte (au moins 300 caractères), "
    "puis /ecris."
)
ALREADY_EMPTY = "Le dossier est déjà vide."
NEXT_STEPS = "/ecris pour écrire l'article, /nouveau pour repartir de zéro."
WRITE_INSIDE_ERROR = (
    "Je n'ai pas réussi à écrire cet article. Le problème vient de mon côté, pas de vos "
    "sources. Renvoyez /ecris dans quelques minutes."
)
SOURCE_INSIDE_ERROR = (
    "Je n'ai pas réussi à ajouter cette source. Le problème vient de mon côté, pas de votre "
    "envoi. Renvoyez-la dans quelques minutes."
)
_HELP = (
    "Envoyez-moi vos sources une par une : le lien d'un article, ou son texte collé (au moins "
    "300 caractères). Chaque envoi s'ajoute au dossier.\n"
    "/ecris : j'écris l'article à partir de toutes les sources.\n"
    "/nouveau : je vide le dossier."
)
_NO_HOST = "Ce lien"
_PASTED_LABEL = "texte collé"
_PAYWALL_FLAG = " Page peut-être réservée aux abonnés : seule la partie visible est gardée."
_TRUNCATED_FLAG = " Texte trop long : seul le début est gardé."
_THOUSANDS_SEPARATOR = " "  # narrow no-break space


def format_chars(count: int) -> str:
    """A number of characters with a narrow no-break space between thousands: `1 800`."""
    return f"{count:,}".replace(",", _THOUSANDS_SEPARATOR)


def _sources(count: int) -> str:
    return "1 source" if count == 1 else f"{count} sources"


def state_line(state: DossierState) -> str:
    if state.count == 0:
        return "Le dossier est vide."
    chars = format_chars(state.total_chars)
    return f"Le dossier contient {_sources(state.count)}, {chars} caractères."


def help_text(state: DossierState) -> str:
    """The private help (ADR-0020, point 2), then the state of the dossier."""
    return f"{_HELP}\n\n{state_line(state)}"


def write_ack(count: int) -> str:
    """The `/ecris` acknowledgment, sent on the first attempt only."""
    return f"J'écris l'article à partir de {_sources(count)}."


def cleared_text(removed: int) -> str:
    """The `/nouveau` answer."""
    if removed == 0:
        return ALREADY_EMPTY
    if removed == 1:
        return "Dossier vidé : 1 source retirée."
    return f"Dossier vidé : {removed} sources retirées."


def thin_warning(count: int, chars: int) -> str:
    """The first line of an article written from thin material (ADR-0020, point 11)."""
    return (
        f"{_WARNING_SIGN}Matière mince ({_sources(count)}, {format_chars(chars)} caractères) : "
        "l'article est court. Ajoutez des sources puis renvoyez /ecris pour un article plus long."
    )


def source_line(link: str) -> str:
    """`Source : <link>`, the link as the journalist sent it (team group)."""
    return f"Source : {link}"


def removed_quotes_line(count: int) -> str:
    """The wording of ADR-0014, point 6."""
    if count == 1:
        return "1 citation retirée : non confirmée par la source."
    return f"{count} citations retirées : non confirmées par la source."


# --- typed errors (ADR-0018, point 13) -------------------------------------------------------


class ErrorFamily(StrEnum):
    LINK = "link"  # the link is wrong: malformed, refused by the URL rule, blocked, not found
    UNSUPPORTED = "unsupported"  # a video or social link
    SITE = "site"  # the site did not let the page be read


def error_family(exc: Exception) -> ErrorFamily | None:
    """The family of a typed error of a link, or None for any other error."""
    if isinstance(exc, InvalidLinkError | BlockedUrlError):
        return ErrorFamily.LINK
    if isinstance(exc, FetchError):
        return ErrorFamily.LINK if exc.reason is FetchReason.NOT_FOUND else ErrorFamily.SITE
    if isinstance(exc, UnsupportedLinkError):
        return ErrorFamily.UNSUPPORTED
    if isinstance(exc, NoReadableTextError):
        return ErrorFamily.SITE
    return None


_GROUP_ERROR_TEXTS = {
    ErrorFamily.LINK: LINK_ERROR,
    ErrorFamily.UNSUPPORTED: UNSUPPORTED_LINK_ERROR,
    ErrorFamily.SITE: SITE_ERROR,
}


def group_error_text(exc: Exception) -> str | None:
    """The team group's message for a typed error, or None for any other error."""
    if isinstance(exc, GenerationFailedError):
        return INSIDE_ERROR
    family = error_family(exc)
    return None if family is None else _GROUP_ERROR_TEXTS[family]


# --- receipt (ADR-0020, point 4) -------------------------------------------------------------

_LINK_ERROR_LINES = {
    ErrorFamily.LINK: (
        "je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il s'ouvre dans un "
        "navigateur, puis renvoyez-le."
    ),
    ErrorFamily.UNSUPPORTED: (
        "je ne lis pas encore les vidéos ni les publications des réseaux sociaux. Collez-moi le "
        "texte ou la légende (au moins 300 caractères) et je l'ajoute au dossier."
    ),
    ErrorFamily.SITE: (
        "le site ne m'a pas laissé lire cette page. Collez-moi le texte de l'article dans un "
        "message et je l'ajoute au dossier."
    ),
}


def added_line(added: Added) -> str:
    label = _PASTED_LABEL if added.kind is SourceKind.PASTED_TEXT else added.host or _NO_HOST
    chars = format_chars(added.stored_chars)
    line = f"Source {added.position} ajoutée : {label}, {chars} caractères."
    if added.paywall_suspected:
        line += _PAYWALL_FLAG
    if added.truncated:
        line += _TRUNCATED_FLAG
    return line


def refused_line(refused: Refused, *, host: str | None, pasted: bool) -> str:
    if pasted:
        return "Texte non ajouté : le dossier est plein."
    name = host or _NO_HOST
    if refused.reason is RefusalReason.ALREADY_THERE:
        return f"{name} : ce lien est déjà dans le dossier."
    return f"{name} : non ajouté, le dossier est plein."


def link_error_line(exc: Exception, host: str | None) -> str:
    """The receipt line of a link that failed with a typed error; ValueError for another."""
    family = error_family(exc)
    if family is None:
        raise ValueError(f"not a typed link error: {type(exc).__name__}")
    return f"{host or _NO_HOST} : {_LINK_ERROR_LINES[family]}"


def receipt(lines: Sequence[str], state: DossierState) -> str:
    """One line per link or text of the message, in order; a blank line; the state of the
    dossier and, when it holds a source, the next steps."""
    tail = state_line(state)
    if state.count > 0:
        tail = f"{tail}\n{NEXT_STEPS}"
    return "\n".join([*lines, "", tail])


# --- links (ADR-0019, point 2) ---------------------------------------------------------------

_LINK = re.compile(r"https?://\S+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}»\"'…"


def find_links(text: str) -> list[str]:
    """Every `http(s)://` link of `text`, in document order, with trailing punctuation removed.

    Trailing characters are removed one at a time from the end. A closing `)` is kept, and
    removal stops, when the link as it stands at that moment holds at least as many `(` as
    `)`: `…/Babylone_(groupe).` gives `…/Babylone_(groupe)`. `]` and `}` are always removed.
    Telegram's own entities are not read: a link written without its scheme is not found."""
    return [_strip_trailing(match.group()) for match in _LINK.finditer(text)]


def _strip_trailing(link: str) -> str:
    while link and link[-1] in _TRAILING:
        if link[-1] == ")" and link.count("(") >= link.count(")"):
            break
        link = link[:-1]
    return link


def host_of(link: str) -> str | None:
    """The host of a link, or None when it has none or does not parse."""
    try:
        return httpx.URL(link).host or None
    except (httpx.InvalidURL, TypeError, ValueError):
        return None


# --- commands (ADR-0020, point 3) ------------------------------------------------------------


class Command(StrEnum):
    WRITE = "write"
    NEW = "new"
    HELP = "help"


_COMMANDS = {"/ecris": Command.WRITE, "/écris": Command.WRITE, "/nouveau": Command.NEW}
_BARE_WORDS = {"ecris": Command.WRITE, "écris": Command.WRITE, "nouveau": Command.NEW}


def parse_command(text: str) -> Command | None:
    """The command of a message, read from its text (not from Telegram's entities).

    The text is NFC-normalized and stripped. A whole message `ecris`, `écris` or `nouveau`,
    compared without case, is that command. Otherwise the first word, when it starts with `/`,
    is compared without case after removing an optional `@<bot name>` suffix: `/ecris` and
    `/écris` write, `/nouveau` empties, any other (`/start` included) gets the help. The text
    after a command is ignored. Anything else is not a command (None)."""
    stripped = unicodedata.normalize("NFC", text).strip()
    bare = _BARE_WORDS.get(stripped.casefold())
    if bare is not None:
        return bare
    words = stripped.split(maxsplit=1)
    if not words or not words[0].startswith("/"):
        return None
    name = words[0].split("@", 1)[0].casefold()
    return _COMMANDS.get(name, Command.HELP)


# --- routing (ADR-0019, point 3; ADR-0020, point 2) ------------------------------------------


class RouteKind(StrEnum):
    LINK = "link"  # team group: a draft from the first link
    SILENT = "silent"  # team group: no answer
    SOURCE = "source"  # private: links, or a pasted text, join the dossier
    WRITE = "write"
    NEW = "new"
    HELP = "help"


_COMMAND_ROUTES = {
    Command.WRITE: RouteKind.WRITE,
    Command.NEW: RouteKind.NEW,
    Command.HELP: RouteKind.HELP,
}


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    link: str | None = None  # LINK: the first link of the message
    several_links: bool = False  # LINK: the message holds more than one link
    links: tuple[str, ...] = ()  # SOURCE: every distinct link in order; () for a pasted text


def route_message(message: InboundMessage) -> Route:
    """Team group: the first link gets a draft, anything else no answer, commands included.

    Private chat, the first rule that applies: a command (`parse_command`); at least one link,
    each distinct link becoming a source; a stripped text of at least MIN_SOURCE_CHARS, a
    pasted-text source; else the help."""
    links = find_links(message.text)
    if not message.is_private:
        if links:
            return Route(RouteKind.LINK, link=links[0], several_links=len(links) > 1)
        return Route(RouteKind.SILENT)
    command = parse_command(message.text)
    if command is not None:
        return Route(_COMMAND_ROUTES[command])
    if links:
        return Route(RouteKind.SOURCE, links=tuple(dict.fromkeys(links)))
    if len(message.text.strip()) >= MIN_SOURCE_CHARS:
        return Route(RouteKind.SOURCE)
    return Route(RouteKind.HELP)


# --- team group reply (ADR-0019, point 7) ----------------------------------------------------


class ReplyWarning(StrEnum):
    PAYWALL = "paywall"
    TRUNCATED = "truncated"


_WARNING_TEXTS = {
    ReplyWarning.PAYWALL: PAYWALL_WARNING,
    ReplyWarning.TRUNCATED: TRUNCATED_WARNING,
}


def warnings_for(draft: Draft) -> list[ReplyWarning]:
    """The warnings that apply, in their order in the reply."""
    warnings: list[ReplyWarning] = []
    if draft.paywall_suspected:
        warnings.append(ReplyWarning.PAYWALL)
    if draft.truncated:
        warnings.append(ReplyWarning.TRUNCATED)
    return warnings


def render_reply(draft: Draft, *, link: str) -> str:
    """The single plain-text reply: the warnings, one per line; the article as `render_text`
    gives it (no evidence marker); the source line (`link` as sent); then the removed-quote
    line when quotes were removed. Blocks are separated by a blank line; the removed-quote line
    follows the source line directly."""
    warnings = warnings_for(draft)
    blocks = []
    if warnings:
        blocks.append("\n".join(_WARNING_TEXTS[warning] for warning in warnings))
    blocks.append(render_text(draft.result.article, draft.evidence))
    tail = source_line(link)
    if draft.result.removed_quotes > 0:
        tail = f"{tail}\n{removed_quotes_line(draft.result.removed_quotes)}"
    blocks.append(tail)
    return "\n\n".join(blocks)


# --- the two messages of /ecris (ADR-0020, point 12) -----------------------------------------

OPENING_CHARS = 40


def opening(text: str) -> str:
    """The first words of a text: whitespace runs collapsed; whole up to OPENING_CHARS
    characters, else cut at the last space at or before that length (at that length when there
    is none) and followed by `…`."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= OPENING_CHARS:
        return collapsed
    cut = collapsed.rfind(" ", 0, OPENING_CHARS + 1)
    if cut <= 0:
        cut = OPENING_CHARS
    return f"{collapsed[:cut].rstrip()}…"


def article_message(written: WrittenArticle) -> str:
    """The article: the thin-material warning and a blank line when the budget is thin, then
    `render_dossier_text`. No marker."""
    text = render_dossier_text(written.result.article, written.evidence)
    if not written.budget.thin:
        return text
    chars = sum(len(source.text) for source in written.sources)
    return f"{thin_warning(len(written.sources), chars)}\n\n{text}"


def _source_entry(number: int, source: Source) -> str:
    flags = []
    if source.paywall_suspected:
        flags.append("page peut-être réservée aux abonnés")
    if source.truncated:
        flags.append("texte coupé")
    suffix = f" ({', '.join(flags)})" if flags else ""
    if source.kind is SourceKind.PASTED_TEXT or source.url is None:
        return f"{number}. Texte collé : {opening(source.text)}{suffix}"
    return f"{number}. {host_of(source.url) or _NO_HOST}{suffix}\n{source.url}"


def sources_message(written: WrittenArticle) -> str:
    """Where each paragraph comes from (ADR-0020, point 12): the numbered sources; one line per
    paragraph with its first words and the numbers of the sources it cites; then the sources
    that no paragraph cites and the removed-quote line, each only when it applies. A source's
    number is its place in the dossier; headings are not listed."""
    blocks = [
        "\n".join(
            [
                "Sources",
                *(_source_entry(n, source) for n, source in enumerate(written.sources, start=1)),
            ]
        )
    ]
    paragraph_lines = ["Paragraphes"]
    for n, (paragraph, cited) in enumerate(
        zip(written.result.article.paragraphs, written.paragraph_sources, strict=True), start=1
    ):
        numbers = ", ".join(str(source) for source in cited) or "aucune source"
        text = render_paragraph(paragraph, written.evidence)
        paragraph_lines.append(f"{n}. {opening(text)} → {numbers}")
    blocks.append("\n".join(paragraph_lines))

    tail: list[str] = []
    cited_anywhere = {source for cited in written.paragraph_sources for source in cited}
    unused = [n for n in range(1, len(written.sources) + 1) if n not in cited_anywhere]
    if len(unused) == 1:
        tail.append(f"Source non utilisée : {unused[0]}")
    elif unused:
        tail.append(f"Sources non utilisées : {', '.join(str(n) for n in unused)}")
    if written.result.removed_quotes > 0:
        tail.append(removed_quotes_line(written.result.removed_quotes))
    if tail:
        blocks.append("\n".join(tail))
    return "\n\n".join(blocks)
