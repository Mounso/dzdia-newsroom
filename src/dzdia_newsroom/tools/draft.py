"""The composition of the slice (ADR-0019, point 12): a link or a pasted text in, a validated
article out. No Telegram or queue knowledge.

Cap and evidence are composed exactly as the v2 arm of the eval does (`cap_text` once, then
`build_evidence` on the capped text, then `generate_article`), so production runs what was
measured. Every error propagates unchanged: the caller maps it to a message.
"""

from dataclasses import dataclass

import httpx

from dzdia_newsroom.llm import StructuredGenerator
from dzdia_newsroom.tools.article import ArticleResult, generate_article
from dzdia_newsroom.tools.evidence import EvidenceSet, build_evidence, cap_text
from dzdia_newsroom.tools.fetch_page import fetch_html
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS, NoReadableTextError, extract_page_text

__all__ = ["Draft", "draft_from_link", "draft_from_text"]


@dataclass(frozen=True)
class Draft:
    result: ArticleResult
    evidence: EvidenceSet
    # Length of the source text before the cap: the extracted page text, or the stripped
    # pasted text.
    source_chars: int
    paywall_suspected: bool
    truncated: bool  # the source was cut by `cap_text` at MAX_INPUT_CHARS


async def draft_from_link(client: httpx.AsyncClient, llm: StructuredGenerator, url: str) -> Draft:
    """Fetch, extract, cap, build evidence, generate. `client` must be the guarded client.

    Raises InvalidLinkError, UnsupportedLinkError, BlockedUrlError, FetchError,
    NoReadableTextError, GenerationFailedError, or whatever the generator raises."""
    page = await fetch_html(client, url)
    text = extract_page_text(page.html)
    return await _compose(llm, text.text, paywall_suspected=text.paywall_suspected)


async def draft_from_text(llm: StructuredGenerator, text: str) -> Draft:
    """The same pipeline from the cap, for a pasted text (ADR-0018, point 12).

    The text is stripped first; shorter than MIN_SOURCE_CHARS, it raises NoReadableTextError
    before any model call. Raises GenerationFailedError, or whatever the generator raises."""
    source = text.strip()
    if len(source) < MIN_SOURCE_CHARS:
        raise NoReadableTextError(f"the pasted text has {len(source)} characters")
    return await _compose(llm, source, paywall_suspected=False)


async def _compose(llm: StructuredGenerator, text: str, *, paywall_suspected: bool) -> Draft:
    capped, truncated = cap_text(text)
    evidence = build_evidence(capped)
    result = await generate_article(llm, evidence)
    return Draft(
        result=result,
        evidence=evidence,
        source_chars=len(text),
        paywall_suspected=paywall_suspected,
        truncated=truncated,
    )
