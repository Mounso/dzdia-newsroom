"""What the bot says and when (ADR-0019, points 2, 3, 7 and 9): the French texts, the link
search, the routing rule and the rendering of the reply. Pure functions, no I/O.

Changing a wording needs no ADR.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from dzdia_newsroom.channels.base import InboundMessage
from dzdia_newsroom.tools.article import render_text
from dzdia_newsroom.tools.draft import Draft
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS

# --- texts (ADR-0019, point 9) ---------------------------------------------------------------

ACK_LINK = "Lien reçu, je prépare un brouillon (environ une minute)."
ACK_SEVERAL_LINKS = (
    "Lien reçu, je prépare un brouillon (environ une minute). "
    "Je ne traite que le premier lien du message."
)
ACK_PASTED_TEXT = "Texte reçu, je prépare un brouillon (environ une minute)."
HELP_TEXT = (
    "Envoyez-moi le lien d'un article, ou collez son texte (au moins 300 caractères) : "
    "je vous renvoie un brouillon avec sa source."
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
LONG_PASTE_WARNING = (
    _WARNING_SIGN
    + "Ce texte est peut-être coupé par Telegram : le brouillon ne couvre que ce message."
)
TRUNCATED_WARNING = _WARNING_SIGN + "Le texte est très long : le brouillon ne couvre que le début."
PASTED_SOURCE_LABEL = "texte collé"

# A pasted text this long (stripped) may have been cut by Telegram (ADR-0018, point 12).
PASTED_TEXT_WARNING_CHARS = 4000


def source_line(link: str | None) -> str:
    """`Source : <link>`, the link as the journalist sent it; None names a pasted text."""
    return f"Source : {link if link is not None else PASTED_SOURCE_LABEL}"


def removed_quotes_line(count: int) -> str:
    """The wording of ADR-0014, point 6."""
    if count == 1:
        return "1 citation retirée : non confirmée par la source."
    return f"{count} citations retirées : non confirmées par la source."


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


# --- routing (ADR-0019, point 3) -------------------------------------------------------------


class RouteKind(StrEnum):
    LINK = "link"
    PASTED_TEXT = "pasted_text"
    HELP = "help"
    SILENT = "silent"


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    link: str | None = None  # the first link, for RouteKind.LINK only
    several_links: bool = False


def route_message(message: InboundMessage) -> Route:
    """A link always wins over the rest of the text, in a private chat and in the team group.
    Private chat: a stripped text of at least MIN_SOURCE_CHARS is a pasted text, anything else
    gets the help. Team group: anything but a link gets no answer."""
    links = find_links(message.text)
    if links:
        return Route(RouteKind.LINK, link=links[0], several_links=len(links) > 1)
    if not message.is_private:
        return Route(RouteKind.SILENT)
    if len(message.text.strip()) >= MIN_SOURCE_CHARS:
        return Route(RouteKind.PASTED_TEXT)
    return Route(RouteKind.HELP)


# --- reply (ADR-0019, point 7) ---------------------------------------------------------------


class ReplyWarning(StrEnum):
    PAYWALL = "paywall"
    LONG_PASTE = "long_paste"
    TRUNCATED = "truncated"


_WARNING_TEXTS = {
    ReplyWarning.PAYWALL: PAYWALL_WARNING,
    ReplyWarning.LONG_PASTE: LONG_PASTE_WARNING,
    ReplyWarning.TRUNCATED: TRUNCATED_WARNING,
}


def warnings_for(draft: Draft, *, pasted: bool) -> list[ReplyWarning]:
    """The warnings that apply, in their order in the reply."""
    warnings: list[ReplyWarning] = []
    if draft.paywall_suspected:
        warnings.append(ReplyWarning.PAYWALL)
    if pasted and draft.source_chars >= PASTED_TEXT_WARNING_CHARS:
        warnings.append(ReplyWarning.LONG_PASTE)
    if draft.truncated:
        warnings.append(ReplyWarning.TRUNCATED)
    return warnings


def render_reply(draft: Draft, *, link: str | None) -> str:
    """The single plain-text reply: the warnings, one per line; the article as `render_text`
    gives it (no evidence marker); the source line (`link` as sent, or None for a pasted text);
    then the removed-quote line when quotes were removed. Blocks are separated by a blank
    line; the removed-quote line follows the source line directly."""
    warnings = warnings_for(draft, pasted=link is None)
    blocks = []
    if warnings:
        blocks.append("\n".join(_WARNING_TEXTS[warning] for warning in warnings))
    blocks.append(render_text(draft.result.article, draft.evidence))
    tail = source_line(link)
    if draft.result.removed_quotes > 0:
        tail = f"{tail}\n{removed_quotes_line(draft.result.removed_quotes)}"
    blocks.append(tail)
    return "\n\n".join(blocks)
