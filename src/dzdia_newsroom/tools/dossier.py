"""The source dossier of a private chat (ADR-0020, points 4, 5, 7, 9, 11 and 16): adding a link,
adding a pasted text, emptying the dossier and writing one article from all of its sources.

No Telegram or queue knowledge: the dossier is reached through the `DossierStore` interface,
implemented on Postgres by `db/dossiers.py`. Fetch, extraction and generation errors propagate
unchanged, as in `tools/draft.py`: the caller maps them to messages.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

import httpx

from dzdia_newsroom.llm import StructuredGenerator
from dzdia_newsroom.tools.dossier_article import (
    DossierArticleResult,
    generate_dossier_article,
    paragraph_sources,
)
from dzdia_newsroom.tools.evidence import (
    MAX_INPUT_CHARS,
    EvidenceSet,
    build_dossier_evidence,
    cap_text,
)
from dzdia_newsroom.tools.fetch_page import check_link, fetch_html
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS, NoReadableTextError, extract_page_text

__all__ = [
    "BUDGET_CEILING_CHARS",
    "DOSSIER_TTL",
    "MAX_DOSSIER_CHARS",
    "MAX_SOURCES",
    "MAX_SOURCE_CHARS",
    "MIN_SOURCE_CHARS",
    "Added",
    "Budget",
    "Candidate",
    "Decision",
    "DossierKey",
    "DossierState",
    "DossierStore",
    "EmptyDossierError",
    "Refusal",
    "RefusalReason",
    "Refused",
    "Source",
    "SourceKind",
    "StoreResult",
    "WrittenArticle",
    "add_link",
    "add_text",
    "clear",
    "decide",
    "length_budget",
    "read_state",
    "refusal_before_fetch",
    "write_article",
]

# --- caps (ADR-0020, points 5 and 7) ---------------------------------------------------------
# Provisional: tuned on tryout dossiers only.

MAX_SOURCES = 6
MAX_SOURCE_CHARS = MAX_INPUT_CHARS  # per source, as for a single draft
MAX_DOSSIER_CHARS = 60_000
# A source is refused when less room than MIN_SOURCE_CHARS is left in the dossier.
DOSSIER_TTL = timedelta(hours=48)  # after the last activity

# --- length budget (ADR-0020, point 11) ------------------------------------------------------
# Provisional guesses, tuned on tryout dossiers only and frozen with the prompt.

BUDGET_LONGEST_WEIGHT = 0.8
BUDGET_OTHERS_WEIGHT = 0.4  # sources on one topic repeat each other
BUDGET_CEILING_CHARS = 6_000
THIN_BUDGET_CHARS = 2_000  # under it: plain paragraphs and a thin-material warning
CHARS_PER_WORD = 6
WORDS_ROUNDING = 50
MIN_WORDS = 100


@dataclass(frozen=True)
class Budget:
    chars: int
    max_words: int
    thin: bool

    @property
    def with_headings(self) -> bool:
        """Headings are requested when the material is not thin (ADR-0020, point 10)."""
        return not self.thin


def length_budget(source_chars: Sequence[int]) -> Budget:
    """The length budget of an article from the stored text lengths of its sources:
    `min(6000, 0.8 L + 0.4 (T - L))` characters, rounded to the nearest integer, with `L` the
    longest and `T` the total; the maximum in words is the budget divided by 6, rounded half up
    to a multiple of 50, at least 100; thin when the budget is under 2,000 characters."""
    longest = max(source_chars, default=0)
    others = sum(source_chars) - longest
    raw = BUDGET_LONGEST_WEIGHT * longest + BUDGET_OTHERS_WEIGHT * others
    chars = min(BUDGET_CEILING_CHARS, round(raw))
    words = int(chars / CHARS_PER_WORD / WORDS_ROUNDING + 0.5) * WORDS_ROUNDING
    return Budget(chars=chars, max_words=max(MIN_WORDS, words), thin=chars < THIN_BUDGET_CHARS)


# --- dossier model ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DossierKey:
    channel: str
    chat_id: str


class SourceKind(StrEnum):
    LINK = "link"
    PASTED_TEXT = "pasted_text"


@dataclass(frozen=True)
class Source:
    position: int  # 1 to n, the number shown to the journalist
    kind: SourceKind
    url: str | None  # the link as sent; None for a pasted text
    text: str  # the extracted text after the caps
    source_chars: int  # its length before the caps
    truncated: bool
    paywall_suspected: bool


@dataclass(frozen=True)
class DossierState:
    """What the caps need to know about the live sources of a dossier."""

    count: int = 0
    total_chars: int = 0  # length of the stored texts
    urls: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Candidate:
    """A source about to be added: its text already cut to MAX_SOURCE_CHARS."""

    kind: SourceKind
    url: str | None
    text: str
    source_chars: int
    truncated: bool
    paywall_suspected: bool


class RefusalReason(StrEnum):
    ALREADY_THERE = "already_there"
    FULL = "full"


@dataclass(frozen=True)
class Refusal:
    reason: RefusalReason


# An accepted candidate, with its final text, or the refusal.
type Decision = Candidate | Refusal


def refusal_before_fetch(state: DossierState, url: str | None) -> Refusal | None:
    """The refusals that do not depend on the text, checked in this order: a link whose url
    equals, as an exact string, a url of the dossier (ALREADY_THERE); then MAX_SOURCES sources,
    or less room than MIN_SOURCE_CHARS left (FULL)."""
    if url is not None and url in state.urls:
        return Refusal(RefusalReason.ALREADY_THERE)
    if state.count >= MAX_SOURCES or MAX_DOSSIER_CHARS - state.total_chars < MIN_SOURCE_CHARS:
        return Refusal(RefusalReason.FULL)
    return None


def decide(state: DossierState, candidate: Candidate) -> Decision:
    """Whether `candidate` joins a dossier in `state` (ADR-0020, points 4 and 5): the refusal of
    `refusal_before_fetch`, else the candidate with its text cut by `cap_text` to the room left
    (MAX_DOSSIER_CHARS minus the stored total); `truncated` is set when either cut happened. A
    text that this cut leaves under MIN_SOURCE_CHARS is refused as FULL."""
    refusal = refusal_before_fetch(state, candidate.url)
    if refusal is not None:
        return refusal
    text, cut = cap_text(candidate.text, MAX_DOSSIER_CHARS - state.total_chars)
    if not cut:
        return candidate
    if len(text) < MIN_SOURCE_CHARS:  # `cap_text` cut at an early line break or space
        return Refusal(RefusalReason.FULL)
    return Candidate(
        kind=candidate.kind,
        url=candidate.url,
        text=text,
        source_chars=candidate.source_chars,
        truncated=True,
        paywall_suspected=candidate.paywall_suspected,
    )


@dataclass(frozen=True)
class StoreResult:
    outcome: Source | Refusal  # the source added, or already there for a replay
    state: DossierState  # the dossier after the call


class DossierStore(Protocol):
    """The dossier's storage (ADR-0020, points 6 and 7). Every operation on a key first drops
    that dossier if it has expired, and then treats it as empty. Only `add_source` (when it
    inserts) and `touch` count as activity."""

    async def add_source(
        self,
        key: DossierKey,
        *,
        message_id: str,
        link_index: int,
        decide: Callable[[DossierState], Decision],
    ) -> StoreResult:
        """In one transaction: lock the dossier row (created if missing), drop it first if it
        has expired, then, when a source with this `message_id` and `link_index` is already
        there (a replay), return it and insert nothing; otherwise call `decide` with the state
        and insert the accepted candidate at position max + 1, or return the refusal."""
        ...

    async def state(self, key: DossierKey) -> DossierState: ...

    async def find_source(
        self, key: DossierKey, *, message_id: str, link_index: int
    ) -> Source | None: ...

    async def sources(self, key: DossierKey) -> list[Source]:
        """The live sources, in position order."""
        ...

    async def touch(self, key: DossierKey) -> None:
        """Mark the activity of a live dossier; an expired or missing one stays as it is."""
        ...

    async def clear(self, key: DossierKey) -> int:
        """Delete the dossier and its sources; returns the number of sources removed."""
        ...


# --- operations ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Added:
    position: int
    kind: SourceKind
    host: str | None  # for a link; None for a pasted text
    stored_chars: int
    truncated: bool
    paywall_suspected: bool
    source_count: int  # the dossier after the add
    total_chars: int


@dataclass(frozen=True)
class Refused:
    reason: RefusalReason


type AddOutcome = Added | Refused


async def add_link(
    store: DossierStore,
    client: httpx.AsyncClient,
    key: DossierKey,
    url: str,
    *,
    message_id: str,
    link_index: int,
) -> AddOutcome:
    """Add the page at `url` to the dossier. `client` must be the guarded client.

    `check_link` first; then a first look at the dossier, without the lock, refuses
    ALREADY_THERE or FULL before any network call, unless this link of this message is already
    stored (a replay: its source is returned). Then `fetch_html`, `extract_page_text`, the cap at
    MAX_SOURCE_CHARS, and the store, whose locked decision is final. The url is stored as sent.

    Raises InvalidLinkError, UnsupportedLinkError, BlockedUrlError, FetchError or
    NoReadableTextError, unchanged."""
    host = check_link(url).host
    refusal = refusal_before_fetch(await store.state(key), url)
    if refusal is not None:
        stored = await store.find_source(key, message_id=message_id, link_index=link_index)
        if stored is None:
            return Refused(refusal.reason)
        return _added(stored, await store.state(key), host=host)

    page = await fetch_html(client, url)
    text = extract_page_text(page.html)
    capped, truncated = cap_text(text.text, MAX_SOURCE_CHARS)
    candidate = Candidate(
        kind=SourceKind.LINK,
        url=url,
        text=capped,
        source_chars=len(text.text),
        truncated=truncated,
        paywall_suspected=text.paywall_suspected,
    )
    return await _store(store, key, candidate, message_id, link_index, host=host)


async def add_text(
    store: DossierStore, key: DossierKey, text: str, *, message_id: str
) -> AddOutcome:
    """Add a pasted text to the dossier (ADR-0020, point 4): stripped, refused with
    NoReadableTextError under MIN_SOURCE_CHARS before the store is reached, cut to
    MAX_SOURCE_CHARS, stored with `link_index` 0."""
    source = text.strip()
    if len(source) < MIN_SOURCE_CHARS:
        raise NoReadableTextError(f"the pasted text has {len(source)} characters")
    capped, truncated = cap_text(source, MAX_SOURCE_CHARS)
    candidate = Candidate(
        kind=SourceKind.PASTED_TEXT,
        url=None,
        text=capped,
        source_chars=len(source),
        truncated=truncated,
        paywall_suspected=False,
    )
    return await _store(store, key, candidate, message_id, 0, host=None)


async def _store(
    store: DossierStore,
    key: DossierKey,
    candidate: Candidate,
    message_id: str,
    link_index: int,
    *,
    host: str | None,
) -> AddOutcome:
    result = await store.add_source(
        key,
        message_id=message_id,
        link_index=link_index,
        decide=lambda state: decide(state, candidate),
    )
    if isinstance(result.outcome, Refusal):
        return Refused(result.outcome.reason)
    return _added(result.outcome, result.state, host=host)


def _added(source: Source, state: DossierState, *, host: str | None) -> Added:
    return Added(
        position=source.position,
        kind=source.kind,
        host=host,
        stored_chars=len(source.text),
        truncated=source.truncated,
        paywall_suspected=source.paywall_suspected,
        source_count=state.count,
        total_chars=state.total_chars,
    )


async def read_state(store: DossierStore, key: DossierKey) -> DossierState:
    """The live state of the dossier, e.g. for the help text: not activity."""
    return await store.state(key)


async def clear(store: DossierStore, key: DossierKey) -> int:
    """Empty the dossier (`/nouveau`); returns the number of sources removed."""
    return await store.clear(key)


class EmptyDossierError(Exception):
    """`/ecris` on a dossier with no live source: no model is called."""


@dataclass(frozen=True)
class WrittenArticle:
    result: DossierArticleResult
    evidence: EvidenceSet
    sources: tuple[Source, ...]  # in position order; evidence `source` n is the n-th of them
    budget: Budget
    paragraph_sources: list[tuple[int, ...]]


async def write_article(
    store: DossierStore, llm: StructuredGenerator, key: DossierKey
) -> WrittenArticle:
    """Write one article from every live source of the dossier (ADR-0020, points 9 to 12).

    Raises EmptyDossierError when there is none, before any model call. Otherwise builds the
    evidence across the sources in position order, computes the budget from the stored text
    lengths, generates with headings unless the budget is thin, then marks the dossier's
    activity. Raises GenerationFailedError, or whatever the generator raises, unchanged; a
    failed generation is not marked as activity."""
    sources = tuple(await store.sources(key))
    if not sources:
        raise EmptyDossierError("the dossier has no source")
    evidence = build_dossier_evidence([source.text for source in sources])
    budget = length_budget([len(source.text) for source in sources])
    result = await generate_dossier_article(
        llm, evidence, max_words=budget.max_words, with_headings=budget.with_headings
    )
    await store.touch(key)
    return WrittenArticle(
        result=result,
        evidence=evidence,
        sources=sources,
        budget=budget,
        paragraph_sources=paragraph_sources(result.article, evidence),
    )
