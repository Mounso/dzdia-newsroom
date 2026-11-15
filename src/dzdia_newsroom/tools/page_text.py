"""Page text extraction shared by production and the eval (ADR-0018, points 9 to 11).

The trafilatura call keeps the parameters measured by the v1 baseline: production runs the
extraction that the eval measured. Production reads the title from the page metadata; the eval
passes the feed headline.
"""

import re
from dataclasses import dataclass

import trafilatura

MIN_SOURCE_CHARS = 300

# Truncated premium articles. The eval refuses them; production warns (ADR-0018, point 10).
PAYWALL_MARKERS = ("lire la suite de cet article", "réservé aux abonnés")

# In-body links to other articles ("🟢 À LIRE AUSSI : <other headline>"): their facts are not part
# of the article and would make unrelated elements look "found".
_RELATED_LINK_LINE = re.compile(r"^\W*[AÀ] LIRE AUSSI\b.*$", re.IGNORECASE | re.MULTILINE)


class NoReadableTextError(Exception):
    """The page gives fewer than `MIN_SOURCE_CHARS` characters of text."""


@dataclass(frozen=True)
class PageText:
    title: str
    text: str
    paywall_suspected: bool  # a warning for the journalist, never a refusal


def clean_text(text: str) -> str:
    text = _RELATED_LINK_LINE.sub("", text)
    return "\n".join(line for line in text.splitlines() if line.strip())


def has_paywall_marker(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in PAYWALL_MARKERS)


def extract_body(html: str) -> str:
    """The cleaned article body, or "" when trafilatura finds none."""
    text = trafilatura.extract(
        html, include_comments=False, include_tables=True, favor_precision=True
    )
    if not text or not text.strip():
        return ""
    return clean_text(str(text))


def join_title(title: str, body: str) -> str:
    """Headline (it carries facts too) + body, unless the body already starts with it."""
    title = title.strip()
    return body if not title or body.startswith(title) else f"{title}\n{body}"


def extract_page_text(html: str) -> PageText:
    """Raises NoReadableTextError when the text is shorter than `MIN_SOURCE_CHARS`."""
    metadata = trafilatura.extract_metadata(html) if html else None
    title = str(metadata.title).strip() if metadata is not None and metadata.title else ""
    text = join_title(title, extract_body(html))
    if len(text) < MIN_SOURCE_CHARS:
        raise NoReadableTextError(f"the page gives {len(text)} characters of text")
    return PageText(title=title, text=text, paywall_suspected=has_paywall_marker(html))
