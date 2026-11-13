"""Try the prompts on pages outside the baseline sample before freezing them (K3a, ADR-0010
point 4). Both prompts were frozen on 2026-11-13 after round 1; their fingerprints are pinned in
`evals/v2/arms.py`. Run `python -m evals.v2.tryout <step> --help`.

Steps:
- `select`: pick the tryout pages from the RSS feeds (3 spike pages given with `--include`, plus
  3 picks by quotes, length and numbers), freeze their text, write the page list (option N2, d3);
- `run`: run the `v2` and `plain_prompt` arms on each page; idempotent;
- `counters`: per-page counters and the verdict of the freeze conditions (option T2);
- `issues`: after a REVISE verdict, the segments the v2 validator flagged (kind, position, id,
  flagged text) and nothing else of the model output: no draft, no rendered or raw text
  (decision U1).

Committed: `evals/datasets/v2_tryout/pages.jsonl` (metadata only). Local only (`evals/.data/`):
the snapshots and the model outputs.
"""

import argparse
import asyncio
import logging
import math
import re
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, Protocol, cast, get_args
from urllib.parse import urlsplit, urlunsplit

import httpx
import trafilatura
from pydantic import BaseModel

from dzdia_newsroom.llm import GeminiClient, StructuredGenerator, TextGenerator, retry_async
from dzdia_newsroom.tools.article import PROMPT_VERSION as V2_PROMPT_VERSION
from dzdia_newsroom.tools.evidence import cap_text
from evals.baseline_v1.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from evals.baseline_v1.paths import Paths as BaselinePaths
from evals.baseline_v1.sample import (
    USER_AGENT,
    FeedEntry,
    FeedUnavailableError,
    fetch_feeds,
    load_sampling_config,
)
from evals.baseline_v1.schemas import SampleItem, SnapshotMeta
from evals.baseline_v1.snapshot import ExtractionError, extract_main_text, sha256_text
from evals.v2.arms import (
    PLAIN_PROMPT_VERSION,
    PLAIN_TEMPERATURE,
    plain_prompt_fingerprint,
    run_plain,
    run_v2,
    v2_prompt_fingerprint,
)

logger = logging.getLogger(__name__)

Arm = Literal["v2", "plain"]
PickReason = Literal["spike", "quotes", "length", "numbers"]
Criterion = Literal["quotes", "length", "numbers"]

HTTP_TIMEOUT = httpx.Timeout(30.0)
PAGE_FETCH_RETRIES = 2
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Picks (option N2): at most this many pages per source, one per criterion, in this order.
# The 3 spike pages are 2 TSA + 1 Algérie 360, so the tryout has 3 pages of each source.
QUOTAS: Mapping[str, int] = {"algerie360": 2, "tsa": 1}
CRITERIA: tuple[Criterion, ...] = ("quotes", "length", "numbers")

# Freeze conditions (option T2, validated by Abdou on 2026-11-13).
MAX_FAILED_V2 = 0  # failed v2 generations
MAX_QUOTATION_MARK_PAGES = 1  # pages with `quotation_marks` at the first attempt
MAX_RETRIED_PAGES = 2  # pages where the retry was used
MAX_REMOVED_QUOTES = 1  # removed quotes, all pages together
MIN_LENGTH_RATIO = Fraction(2, 3)  # median v2 output length / median plain output length

V2_SETTINGS: dict[str, float | str] = {"temperature": 0.0, "response_schema": "ArticleDraft"}
PLAIN_SETTINGS: dict[str, float | str] = {"temperature": PLAIN_TEMPERATURE}

_EVALS_DIR = Path(__file__).resolve().parent.parent


class TryoutError(RuntimeError):
    """A step cannot go on; the message says why. `main` prints it and exits."""


@dataclass(frozen=True)
class TryoutPaths:
    dataset_dir: Path  # committed: the page list
    data_dir: Path  # gitignored: snapshots and model outputs
    baseline: BaselinePaths  # read only: feeds, sample, snapshot hashes

    @classmethod
    def default(cls) -> "TryoutPaths":
        return cls(
            dataset_dir=_EVALS_DIR / "datasets" / "v2_tryout",
            data_dir=_EVALS_DIR / ".data" / "v2_tryout",
            baseline=BaselinePaths.default(),
        )

    @property
    def pages(self) -> Path:
        return self.dataset_dir / "pages.jsonl"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def runs(self) -> Path:
        return self.data_dir / "runs.jsonl"

    def html_path(self, page_id: str) -> Path:
        return self.snapshot_dir / f"{page_id}.html"

    def text_path(self, page_id: str) -> Path:
        return self.snapshot_dir / f"{page_id}.txt"


# --- records ---------------------------------------------------------------------------------


class TryoutPage(BaseModel):
    page_id: str
    source: str
    url: str
    final_url: str
    title: str
    published: str | None = None
    picked_for: PickReason
    fetched_at: datetime
    text_sha256: str
    n_chars: int
    n_quoted: int
    n_numbers: int


class IssueDetailRecord(BaseModel):
    """One issue of an attempt (decision U1): read by the `issues` step."""

    kind: str
    paragraph: int | None = None
    segment: int | None = None
    text: str = ""  # the flagged segment's text, or the title
    detail: str = ""  # e.g. the unknown or cited evidence id


class AttemptRecord(BaseModel):
    issues: dict[str, int]  # number of issues per kind
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: int | None = None
    raw_text: str | None = None  # unparsable outputs only
    # In the order the validator reports them; empty in records written before decision U1.
    issue_details: list[IssueDetailRecord] = []


class TryoutRun(BaseModel):
    run_id: str
    page_id: str
    arm: Arm
    model: str
    prompt_version: str
    prompt_fingerprint: str
    settings: dict[str, float | str]  # what was actually sent (ADR-0010, point 13)
    created_at: datetime
    truncated: bool  # the page text was cut by `cap_text`
    n_source_chars: int  # length of the capped text the arms received
    error: str | None = None  # technical error (API, timeout): re-running the step retries it
    # Both arms, when there is an output. v2: summed over the attempts.
    n_output_chars: int | None = None  # length of the stripped output (v2: rendered) text
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: int | None = None
    # v2 only
    outcome: Literal["ok", "failed"] | None = None
    n_evidence: int | None = None
    attempts: list[AttemptRecord] = []
    retried: bool | None = None
    removed_quotes: int | None = None
    kept_quotes: int | None = None
    n_sentences: int | None = None
    rendered_text: str | None = None
    draft: dict[str, Any] | None = None
    # plain only
    output_text: str | None = None


# --- out-of-sample guard ---------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Lowercase scheme and host, no leading `www.`, no fragment, no trailing slash. The query
    is kept: opaque `?p=ID` permalinks are caught through the final URL."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit((parts.scheme.lower(), host, parts.path.removesuffix("/"), parts.query, ""))


def parse_published(published: str | None) -> datetime | None:
    """An RFC 822 feed date as an aware datetime (UTC assumed without a zone); None if absent
    or unreadable."""
    if not published:
        return None
    try:
        parsed = parsedate_to_datetime(published)
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class SampleGuard:
    """What the tryout must stay away from: the baseline sample (ADR-0010, point 4)."""

    urls: frozenset[str]  # normalized
    latest_published: datetime | None

    @classmethod
    def from_baseline(
        cls, sample: Iterable[SampleItem], snapshots: Iterable[SnapshotMeta]
    ) -> "SampleGuard":
        sample = list(sample)
        urls = {normalize_url(item.url) for item in sample}
        for meta in snapshots:
            urls |= {normalize_url(meta.url), normalize_url(meta.final_url)}
        dates = [d for d in (parse_published(item.published) for item in sample) if d]
        return cls(urls=frozenset(urls), latest_published=max(dates) if dates else None)

    def refusal(
        self, url: str, *, final_url: str | None = None, published: str | None = None
    ) -> str | None:
        """Why the page is refused, or None. A page with no known date is not refused for its
        date: a human validates the list."""
        if normalize_url(url) in self.urls:
            return "URL of the baseline sample"
        if final_url is not None and normalize_url(final_url) in self.urls:
            return f"final URL {final_url} is in the baseline sample"
        date = parse_published(published)
        latest = self.latest_published
        if date is not None and latest is not None and date <= latest:
            return f"published {published}, not after the sample's latest ({latest:%Y-%m-%d %H:%M})"
        return None


# --- metrics ---------------------------------------------------------------------------------

# A quoted passage stays on one line: an unbalanced mark must not swallow a whole page.
_QUOTED = re.compile(r"«([^«»\n]*)»|“([^“”\n]*)”|\"([^\"\n]*)\"")
_DIGITS = re.compile(r"\d+")
MIN_QUOTED_WORDS = 3


def count_quoted(text: str) -> int:
    """Passages of at least `MIN_QUOTED_WORDS` words inside `«…»`, `“…”` or `"…"`."""
    return sum(
        len(next(group for group in match.groups() if group is not None).split())
        >= MIN_QUOTED_WORDS
        for match in _QUOTED.finditer(text)
    )


def count_numbers(text: str) -> int:
    """Digit runs: `3 000` counts two, `2026` one."""
    return len(_DIGITS.findall(text))


# --- candidates and picks --------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    source: str
    url: str
    final_url: str
    title: str
    published: str | None
    html: str
    text: str
    fetched_at: datetime

    @property
    def published_at(self) -> datetime | None:
        return parse_published(self.published)

    @property
    def n_chars(self) -> int:
        return len(self.text)

    @property
    def n_quoted(self) -> int:
        return count_quoted(self.text)

    @property
    def n_numbers(self) -> int:
        return count_numbers(self.text)

    def metric(self, criterion: Criterion) -> int:
        match criterion:
            case "quotes":
                return self.n_quoted
            case "length":
                return self.n_chars
            case "numbers":
                return self.n_numbers


def _recency_key(candidate: Candidate) -> float:
    """Sorts the most recent first; an unknown date last."""
    date = candidate.published_at
    return -date.timestamp() if date else math.inf


def _rank_key(candidate: Candidate, criterion: Criterion) -> tuple[int, float, str]:
    """Best metric first, then the most recent, then the URL in lexical order."""
    return (-candidate.metric(criterion), _recency_key(candidate), candidate.url)


def pick_pages(
    candidates: Sequence[Candidate],
    quotas: Mapping[str, int] = QUOTAS,
    criteria: Sequence[Criterion] = CRITERIA,
) -> list[tuple[Candidate, Criterion]]:
    """For each criterion in order, the best remaining candidate whose source still has quota.
    A criterion with no such candidate picks nothing."""
    left = dict(quotas)
    picked: list[tuple[Candidate, Criterion]] = []
    for criterion in criteria:
        eligible = [
            c
            for c in candidates
            if left.get(c.source, 0) > 0 and all(c is not p for p, _ in picked)
        ]
        if not eligible:
            continue
        best = min(eligible, key=lambda c: _rank_key(c, criterion))
        left[best.source] -= 1
        picked.append((best, criterion))
    return picked


def source_of(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return {"tsa-algerie.com": "tsa", "algerie360.com": "algerie360"}.get(host, host)


def _is_retryable_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[str, str]:
    """The HTML and the final URL after redirects, with bounded retries on 429/5xx and transport
    errors. The client carries the timeout."""

    async def once() -> tuple[str, str]:
        start = time.perf_counter()
        try:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "page fetch -> %s",
                type(exc).__name__,
                extra={"url": url, "latency_ms": int((time.perf_counter() - start) * 1000)},
            )
            raise
        logger.info(
            "page fetch -> ok",
            extra={"url": url, "latency_ms": int((time.perf_counter() - start) * 1000)},
        )
        return response.text, str(response.url)

    return await retry_async(
        once, is_retryable=_is_retryable_fetch_error, max_retries=PAGE_FETCH_RETRIES, sleep=sleep
    )


@dataclass
class Selection:
    candidates: list[Candidate] = field(default_factory=list)  # downloaded feed candidates
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (url, reason)
    pages: list[TryoutPage] = field(default_factory=list)


async def _download(
    client: httpx.AsyncClient,
    url: str,
    source: str,
    title: str | None,
    published: str | None,
    sleep: Callable[[float], Awaitable[None]],
) -> Candidate:
    """Raises httpx.HTTPError or ExtractionError (PaywallError included)."""
    html, final_url = await fetch_page(client, url, sleep=sleep)
    if title is None:
        metadata = trafilatura.extract_metadata(html)
        title = str(metadata.title) if metadata is not None and metadata.title else ""
    return Candidate(
        source=source,
        url=url,
        final_url=final_url,
        title=title,
        published=published,
        html=html,
        text=extract_main_text(html, title),
        fetched_at=datetime.now(UTC),
    )


def _to_page(candidate: Candidate, page_id: str, picked_for: PickReason) -> TryoutPage:
    return TryoutPage(
        page_id=page_id,
        source=candidate.source,
        url=candidate.url,
        final_url=candidate.final_url,
        title=candidate.title,
        published=candidate.published,
        picked_for=picked_for,
        fetched_at=candidate.fetched_at,
        text_sha256=sha256_text(candidate.text),
        n_chars=candidate.n_chars,
        n_quoted=candidate.n_quoted,
        n_numbers=candidate.n_numbers,
    )


async def select_pages(
    paths: TryoutPaths,
    client: httpx.AsyncClient,
    *,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    candidates_per_feed: int = 15,
    force: bool = False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Selection:
    """The `select` step: guard, download, pick, then write the snapshots and `pages.jsonl`."""
    if paths.pages.exists() and not force:
        raise TryoutError(f"{paths.pages} exists: the page list is frozen. Use --force to redo it.")
    guard = SampleGuard.from_baseline(
        read_jsonl(paths.baseline.sample, SampleItem),
        read_jsonl(paths.baseline.snapshots_meta, SnapshotMeta),
    )
    config = load_sampling_config(paths.baseline.feeds)
    try:
        feeds = await fetch_feeds(config, client)
    except FeedUnavailableError as exc:
        raise TryoutError(str(exc)) from exc
    in_feeds: dict[str, tuple[str, FeedEntry]] = {}
    for name, entries in feeds.items():
        for entry in entries:
            in_feeds.setdefault(normalize_url(entry.link), (name, entry))

    selection = Selection()
    spikes: list[Candidate] = []
    for url in include:
        known = in_feeds.get(normalize_url(url))
        title, published = (known[1].title, known[1].published) if known else (None, None)
        reason = guard.refusal(url, published=published)
        if reason:
            raise TryoutError(f"--include {url} is refused: {reason}")
        try:
            spike = await _download(client, url, source_of(url), title, published, sleep)
        except (httpx.HTTPError, ExtractionError) as exc:
            raise TryoutError(f"--include {url} failed: {exc!r}") from exc
        reason = guard.refusal(url, final_url=spike.final_url)
        if reason:
            raise TryoutError(f"--include {url} is refused: {reason}")
        spikes.append(spike)

    left_out = {normalize_url(url) for url in [*include, *exclude]}
    excluded = {normalize_url(url) for url in exclude}
    seen: set[str] = set()
    for feed in config.feeds:
        for entry in feeds[feed.name][:candidates_per_feed]:
            key = normalize_url(entry.link)
            if key in seen:
                continue
            seen.add(key)
            if key in left_out:
                if key in excluded:
                    selection.skipped.append((entry.link, "excluded by --exclude"))
                continue
            reason = guard.refusal(entry.link, published=entry.published)
            if reason:
                selection.skipped.append((entry.link, f"refused: {reason}"))
                continue
            try:
                candidate = await _download(
                    client, entry.link, feed.name, entry.title, entry.published, sleep
                )
            except (httpx.HTTPError, ExtractionError) as exc:
                selection.skipped.append((entry.link, f"skipped: {exc!r}"))
                continue
            reason = guard.refusal(entry.link, final_url=candidate.final_url)
            if reason:
                selection.skipped.append((entry.link, f"refused: {reason}"))
                continue
            selection.candidates.append(candidate)

    retained: list[tuple[Candidate, PickReason]] = [(spike, "spike") for spike in spikes]
    retained += pick_pages(selection.candidates)
    paths.snapshot_dir.mkdir(parents=True, exist_ok=True)
    for index, (candidate, picked_for) in enumerate(retained):
        page = _to_page(candidate, f"tryout-{index:02d}", picked_for)
        # Bytes, so that Windows does not turn "\n" into "\r\n" (the hash is of the text).
        paths.html_path(page.page_id).write_bytes(candidate.html.encode("utf-8"))
        paths.text_path(page.page_id).write_bytes(candidate.text.encode("utf-8"))
        selection.pages.append(page)
    write_jsonl(paths.pages, selection.pages)
    return selection


def print_selection(selection: Selection, sample: Sequence[SampleItem]) -> None:
    """Counters and titles only: no article text."""
    print("Candidates (source, published, n_chars, n_quoted, n_numbers, title):")
    for c in sorted(selection.candidates, key=lambda c: (c.source, _recency_key(c))):
        print(
            f"  {c.source:<11} {c.published or '':<32} {c.n_chars:>6} {c.n_quoted:>3} "
            f"{c.n_numbers:>4}  {c.title}"
        )
    print(f"Skipped or refused ({len(selection.skipped)}):")
    for url, reason in selection.skipped:
        print(f"  {url}: {reason}")
    print(f"Retained ({len(selection.pages)}):")
    for page in selection.pages:
        print(
            f"  {page.page_id} {page.picked_for:<7} {page.source:<11} {page.published or '':<32} "
            f"{page.n_chars:>6} {page.n_quoted:>3} {page.n_numbers:>4}  {page.title}"
        )
        print(f"           {page.url}")
    if len(selection.pages) != len(CRITERIA) + 3:
        print(f"WARNING: {len(selection.pages)} pages retained, {len(CRITERIA) + 3} expected.")
    print("Baseline sample titles (look for a follow-up of a sampled story):")
    for item in sample:
        if not item.is_opaque:
            print(f"  {item.item_id:<14} {item.published or '':<32} {item.title}")


# --- run -------------------------------------------------------------------------------------


class TryoutLLM(TextGenerator, StructuredGenerator, Protocol):
    """Both arms run on the same client."""


def load_page_text(paths: TryoutPaths, page: TryoutPage) -> str:
    """The frozen text, checked against its hash in `pages.jsonl`."""
    path = paths.text_path(page.page_id)
    if not path.exists():
        raise TryoutError(f"Missing snapshot {path}: run 'select' again.")
    text = path.read_bytes().decode("utf-8")
    if sha256_text(text) != page.text_sha256:
        raise TryoutError(
            f"{path} does not match text_sha256 of {page.page_id} in {paths.pages}: the frozen "
            "text changed."
        )
    return text


def run_id_for(page_id: str, arm: Arm, fingerprint: str, model: str) -> str:
    return f"{page_id}:{arm}:{fingerprint[:12]}:{model}"


async def _execute(
    llm: TryoutLLM, page: TryoutPage, arm: Arm, text: str, truncated: bool, fingerprint: str
) -> TryoutRun:
    record = TryoutRun(
        run_id=run_id_for(page.page_id, arm, fingerprint, llm.model),
        page_id=page.page_id,
        arm=arm,
        model=llm.model,
        prompt_version=V2_PROMPT_VERSION if arm == "v2" else PLAIN_PROMPT_VERSION,
        prompt_fingerprint=fingerprint,
        settings=V2_SETTINGS if arm == "v2" else PLAIN_SETTINGS,
        created_at=datetime.now(UTC),
        truncated=truncated,
        n_source_chars=len(text),
    )
    try:
        if arm == "plain":
            plain = await run_plain(llm, text)
            return record.model_copy(
                update={
                    "output_text": plain.text,
                    "n_output_chars": len(plain.text.strip()),
                    "input_tokens": plain.usage.input_tokens,
                    "output_tokens": plain.usage.output_tokens,
                    "latency_ms": plain.latency_ms,
                }
            )
        v2 = await run_v2(llm, text)
    except Exception as exc:  # a technical error must not abort the batch
        return record.model_copy(update={"error": repr(exc)})
    usage = v2.usage
    return record.model_copy(
        update={
            "outcome": v2.outcome,
            "n_evidence": v2.n_evidence,
            "attempts": [
                AttemptRecord(
                    issues=a.issues,
                    input_tokens=a.usage.input_tokens,
                    output_tokens=a.usage.output_tokens,
                    thinking_tokens=a.usage.thinking_tokens,
                    latency_ms=a.latency_ms,
                    raw_text=a.raw_text,
                    issue_details=[
                        IssueDetailRecord(
                            kind=d.kind,
                            paragraph=d.paragraph,
                            segment=d.segment,
                            text=d.text,
                            detail=d.detail,
                        )
                        for d in a.issue_details
                    ],
                )
                for a in v2.attempts
            ],
            "retried": v2.retried,
            "removed_quotes": v2.removed_quotes,
            "kept_quotes": v2.kept_quotes,
            "n_sentences": v2.n_sentences,
            "rendered_text": v2.rendered_text,
            "draft": v2.draft,
            "n_output_chars": len(v2.rendered_text.strip()) if v2.rendered_text else None,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "thinking_tokens": usage.thinking_tokens,
            "latency_ms": v2.latency_ms,
        }
    )


def fingerprint_of(arm: Arm) -> str:
    return v2_prompt_fingerprint() if arm == "v2" else plain_prompt_fingerprint()


def run_line(record: TryoutRun) -> str:
    """One line of counters, no model text."""
    head = f"{record.page_id} {record.arm:<5}"
    if record.error:
        return f"{head} ERROR {record.error[:200]}"
    latency = f"{(record.latency_ms or 0) / 1000:.1f} s"
    if record.arm == "plain":
        return f"{head} {record.n_output_chars} chars, {latency}"
    issues = " | ".join(_kinds(a.issues) for a in record.attempts)
    return (
        f"{head} {record.outcome}, retried {'yes' if record.retried else 'no'}, issues {issues}, "
        f"kept {record.kept_quotes}, removed {record.removed_quotes}, "
        f"{record.n_output_chars} chars, {latency}"
    )


async def run_tryout(
    paths: TryoutPaths,
    llm: TryoutLLM,
    *,
    arms: Sequence[Arm] = ("v2", "plain"),
    page_ids: Sequence[str] = (),
    on_record: Callable[[TryoutRun], None] = lambda record: None,
) -> list[TryoutRun]:
    """The `run` step. Every page text is checked before the first call. A run_id already
    recorded without error is skipped; each record is appended as soon as its call ends."""
    pages = read_jsonl(paths.pages, TryoutPage)
    if not pages:
        raise TryoutError(f"No page in {paths.pages}: run 'select' first.")
    if page_ids:
        unknown = sorted(set(page_ids) - {p.page_id for p in pages})
        if unknown:
            raise TryoutError(f"Unknown page ids: {unknown}")
        pages = [p for p in pages if p.page_id in page_ids]
    texts = {page.page_id: cap_text(load_page_text(paths, page)) for page in pages}
    fingerprints = {arm: fingerprint_of(arm) for arm in arms}
    previous = latest_by(read_jsonl(paths.runs, TryoutRun), lambda r: r.run_id)
    done = {run_id for run_id, r in previous.items() if r.error is None}

    records: list[TryoutRun] = []
    for page in pages:
        text, truncated = texts[page.page_id]
        for arm in arms:
            if run_id_for(page.page_id, arm, fingerprints[arm], llm.model) in done:
                continue
            record = await _execute(llm, page, arm, text, truncated, fingerprints[arm])
            append_jsonl(paths.runs, [record])
            on_record(record)
            records.append(record)
    return records


# --- counters and verdict --------------------------------------------------------------------


def _kinds(issues: Mapping[str, int]) -> str:
    return ",".join(f"{kind}={n}" for kind, n in sorted(issues.items())) or "-"


def latest_fingerprints(runs: Iterable[TryoutRun]) -> dict[Arm, str]:
    """For each arm, the fingerprint of its most recent record."""
    latest: dict[Arm, TryoutRun] = {}
    for run in runs:
        if run.arm not in latest or run.created_at >= latest[run.arm].created_at:
            latest[run.arm] = run
    return {arm: run.prompt_fingerprint for arm, run in latest.items()}


def records_for(
    runs: Iterable[TryoutRun], arm: Arm, fingerprint: str | None
) -> dict[str, TryoutRun]:
    """The most recent record of each page for `arm` and `fingerprint`."""
    chosen: dict[str, TryoutRun] = {}
    for run in sorted(runs, key=lambda r: r.created_at):
        if run.arm == arm and run.prompt_fingerprint == fingerprint:
            chosen[run.page_id] = run
    return chosen


@dataclass(frozen=True)
class ConditionCheck:
    name: str
    observed: str
    passed: bool


@dataclass(frozen=True)
class Verdict:
    conditions: tuple[ConditionCheck, ...]
    missing: tuple[str, ...]  # "<page_id> <arm>" with no record, or a technical error only
    status: Literal["FREEZE", "REVISE", "INCOMPLETE"]


def freeze_verdict(
    page_ids: Sequence[str], v2: Mapping[str, TryoutRun], plain: Mapping[str, TryoutRun]
) -> Verdict:
    """The five freeze conditions over the records without technical error, then FREEZE if they
    all hold and every page has such a record in both arms, INCOMPLETE if a record is missing or
    in technical error, else REVISE."""
    v2_ok = {p: r for p, r in v2.items() if p in page_ids and r.error is None}
    plain_ok = {p: r for p, r in plain.items() if p in page_ids and r.error is None}
    missing = tuple(
        f"{page_id} {arm}"
        for page_id in page_ids
        for arm, records in (("v2", v2_ok), ("plain", plain_ok))
        if page_id not in records
    )

    failed = sum(r.outcome == "failed" for r in v2_ok.values())
    marked = sum(
        bool(r.attempts and r.attempts[0].issues.get("quotation_marks")) for r in v2_ok.values()
    )
    retried = sum(bool(r.retried) for r in v2_ok.values())
    removed = sum(r.removed_quotes or 0 for r in v2_ok.values())
    paired = [
        p
        for p in page_ids
        if p in v2_ok
        and v2_ok[p].outcome == "ok"
        and v2_ok[p].n_output_chars is not None
        and p in plain_ok
        and plain_ok[p].n_output_chars is not None
    ]
    if paired:
        median_v2 = Fraction(statistics.median(v2_ok[p].n_output_chars or 0 for p in paired))
        median_plain = Fraction(statistics.median(plain_ok[p].n_output_chars or 0 for p in paired))
        length = ConditionCheck(
            "median v2 length >= 2/3 of median plain length",
            f"{float(median_v2):g} vs {float(median_plain):g} on {len(paired)} pages",
            median_v2 >= MIN_LENGTH_RATIO * median_plain,
        )
    else:
        length = ConditionCheck(
            "median v2 length >= 2/3 of median plain length", "no paired page", False
        )
    conditions = (
        ConditionCheck("no failed v2 generation", f"{failed} failed", failed <= MAX_FAILED_V2),
        ConditionCheck(
            f"quotation_marks at attempt 1 on <= {MAX_QUOTATION_MARK_PAGES} page",
            f"{marked} pages",
            marked <= MAX_QUOTATION_MARK_PAGES,
        ),
        ConditionCheck(
            f"retry used on <= {MAX_RETRIED_PAGES} pages",
            f"{retried} pages",
            retried <= MAX_RETRIED_PAGES,
        ),
        ConditionCheck(
            f"<= {MAX_REMOVED_QUOTES} removed quote in total",
            f"{removed} removed",
            removed <= MAX_REMOVED_QUOTES,
        ),
        length,
    )
    if missing:
        status: Literal["FREEZE", "REVISE", "INCOMPLETE"] = "INCOMPLETE"
    elif all(c.passed for c in conditions):
        status = "FREEZE"
    else:
        status = "REVISE"
    return Verdict(conditions=conditions, missing=missing, status=status)


def print_counters(
    page_ids: Sequence[str], v2: Mapping[str, TryoutRun], plain: Mapping[str, TryoutRun]
) -> None:
    print("page       | v2: outcome retried issues@1 | issues@2 kept removed sentences chars")
    print("           | plain: chars error")
    totals = {"retried": 0, "kept": 0, "removed": 0, "failed": 0}
    for page_id in page_ids:
        r = v2.get(page_id)
        if r is None:
            v2_cell = "no record"
        elif r.error:
            v2_cell = f"ERROR {r.error[:80]}"
        else:
            attempt_2 = _kinds(r.attempts[1].issues) if len(r.attempts) > 1 else "-"
            v2_cell = (
                f"{r.outcome} {'yes' if r.retried else 'no'} {_kinds(r.attempts[0].issues)} | "
                f"{attempt_2} {r.kept_quotes} {r.removed_quotes} {r.n_sentences} "
                f"{r.n_output_chars}"
            )
            totals["retried"] += bool(r.retried)
            totals["kept"] += r.kept_quotes or 0
            totals["removed"] += r.removed_quotes or 0
            totals["failed"] += r.outcome == "failed"
        p = plain.get(page_id)
        plain_cell = (
            "no record" if p is None else f"{p.n_output_chars} {p.error[:80] if p.error else '-'}"
        )
        print(f"{page_id:<10} | v2: {v2_cell}")
        print(f"{'':<10} | plain: {plain_cell}")
    print(
        f"Totals v2: {totals['failed']} failed, {totals['retried']} retried, "
        f"{totals['kept']} kept quotes, {totals['removed']} removed quotes"
    )


def print_verdict(verdict: Verdict) -> None:
    for number, check in enumerate(verdict.conditions, start=1):
        print(f"  {number}. {'ok  ' if check.passed else 'FAIL'} {check.name}: {check.observed}")
    if verdict.missing:
        print(f"  Missing or in technical error: {', '.join(verdict.missing)}")
    print(verdict.status)


def counters(
    paths: TryoutPaths, fingerprint_v2: str | None = None, fingerprint_plain: str | None = None
) -> Verdict:
    """The `counters` step: prints the table and the verdict, and returns the verdict."""
    page_ids = [page.page_id for page in read_jsonl(paths.pages, TryoutPage)]
    if not page_ids:
        raise TryoutError(f"No page in {paths.pages}: run 'select' first.")
    runs = _read_runs(paths)
    v2 = records_for(runs, "v2", _choose_fingerprint(runs, "v2", fingerprint_v2))
    plain = records_for(runs, "plain", _choose_fingerprint(runs, "plain", fingerprint_plain))
    print_counters(page_ids, v2, plain)
    verdict = freeze_verdict(page_ids, v2, plain)
    print_verdict(verdict)
    return verdict


def _read_runs(paths: TryoutPaths) -> list[TryoutRun]:
    return list(latest_by(read_jsonl(paths.runs, TryoutRun), lambda r: r.run_id).values())


def _choose_fingerprint(runs: Sequence[TryoutRun], arm: Arm, given: str | None) -> str | None:
    """`given`, else the fingerprint of the arm's most recent record; prints the choice and the
    other fingerprints present."""
    chosen = given or latest_fingerprints(runs).get(arm)
    others = sorted({r.prompt_fingerprint for r in runs if r.arm == arm} - {chosen})
    print(f"{arm}: fingerprint {chosen} (others present: {', '.join(others) or '-'})")
    return chosen


# --- flagged segments (decision U1) ----------------------------------------------------------


def _position(detail: IssueDetailRecord) -> str:
    if detail.paragraph is not None:
        return f"p{detail.paragraph}.s{detail.segment}"
    if detail.kind == "quotation_marks":
        return "title"
    return "-"


def issue_lines(
    page_ids: Sequence[str], v2: Mapping[str, TryoutRun], only: Sequence[str] = ()
) -> list[str]:
    """One line per issue of each attempt, for the pages that have a v2 record (restricted to
    `only` when given): page, attempt, kind, position, detail, then the flagged text. Nothing
    else of the model output: an unparsable attempt shows the length of its raw text only."""
    lines: list[str] = []
    for page_id in page_ids:
        record = v2.get(page_id)
        if record is None or (only and page_id not in only):
            continue
        if record.error is not None:
            lines.append(f"{page_id} no attempt (technical error)")
            continue
        if not any(attempt.issues for attempt in record.attempts):
            lines.append(f"{page_id} no issue")
            continue
        for number, attempt in enumerate(record.attempts, start=1):
            head = f"{page_id} attempt {number}"
            if attempt.issues and not attempt.issue_details:
                lines.append(f"{head} {_kinds(attempt.issues)} (details not recorded)")
            for d in attempt.issue_details:
                if d.kind == "unparsable":
                    lines.append(f"{head} unparsable raw text {len(attempt.raw_text or '')} chars")
                    continue
                text = " ".join(d.text.split())
                lines.append(f"{head} {d.kind} {_position(d)} {d.detail or '-'} | {text}")
    return lines


def issues(
    paths: TryoutPaths, fingerprint_v2: str | None = None, page_ids: Sequence[str] = ()
) -> list[str]:
    """The `issues` step: prints the flagged segments of the v2 records and returns the lines."""
    pages = [page.page_id for page in read_jsonl(paths.pages, TryoutPage)]
    if not pages:
        raise TryoutError(f"No page in {paths.pages}: run 'select' first.")
    unknown = sorted(set(page_ids) - set(pages))
    if unknown:
        raise TryoutError(f"Unknown page ids: {unknown}")
    runs = _read_runs(paths)
    v2 = records_for(runs, "v2", _choose_fingerprint(runs, "v2", fingerprint_v2))
    lines = issue_lines(pages, v2, page_ids)
    for line in lines:
        print(line)
    return lines


# --- CLI -------------------------------------------------------------------------------------


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True, max_redirects=5)


def _gemini() -> GeminiClient:
    from dzdia_newsroom.config import get_llm_settings

    settings = get_llm_settings()
    return GeminiClient(
        settings.gemini_api_key.get_secret_value(),
        model=settings.generation_model,
        max_concurrency=settings.llm_max_concurrency,
        max_rpm=settings.llm_max_rpm,
        timeout_s=settings.llm_timeout_s,
    )


async def cmd_select(
    paths: TryoutPaths,
    include: Sequence[str],
    exclude: Sequence[str],
    candidates_per_feed: int,
    force: bool,
) -> None:
    async with _http_client() as client:
        selection = await select_pages(
            paths,
            client,
            include=include,
            exclude=exclude,
            candidates_per_feed=candidates_per_feed,
            force=force,
        )
    print_selection(selection, read_jsonl(paths.baseline.sample, SampleItem))
    print(f"Page list written: {len(selection.pages)} pages -> {paths.pages}")


async def cmd_run(paths: TryoutPaths, arms: Sequence[Arm], page_ids: Sequence[str]) -> None:
    llm = _gemini()
    print(f"Model {llm.model}, arms {', '.join(arms)}")
    records = await run_tryout(
        paths, llm, arms=arms, page_ids=page_ids, on_record=lambda r: print(run_line(r))
    )
    errors = sum(r.error is not None for r in records)
    print(f"Executed {len(records)} runs, {errors} errors (re-run the step to retry them).")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evals.v2.tryout",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    steps = parser.add_subparsers(dest="step", required=True)
    select = steps.add_parser("select", help="Pick and freeze the tryout pages")
    select.add_argument("--include", nargs="*", default=[], metavar="URL", help="Spike pages")
    select.add_argument("--exclude", nargs="*", default=[], metavar="URL")
    select.add_argument("--candidates-per-feed", type=int, default=15)
    select.add_argument("--force", action="store_true", help="Overwrite an existing page list")
    run = steps.add_parser("run", help="Run the arms on the tryout pages")
    run.add_argument("--arms", nargs="+", choices=get_args(Arm), default=list(get_args(Arm)))
    run.add_argument("--pages", nargs="+", default=[], metavar="PAGE_ID")
    count = steps.add_parser("counters", help="Per-page counters and the freeze verdict")
    count.add_argument("--fingerprint-v2")
    count.add_argument("--fingerprint-plain")
    flagged = steps.add_parser("issues", help="The segments the v2 validator flagged, only")
    flagged.add_argument("--fingerprint-v2")
    flagged.add_argument("--pages", nargs="+", default=[], metavar="PAGE_ID")

    args = parser.parse_args(argv)
    paths = TryoutPaths.default()
    try:
        match args.step:
            case "select":
                asyncio.run(
                    cmd_select(
                        paths, args.include, args.exclude, args.candidates_per_feed, args.force
                    )
                )
            case "run":
                asyncio.run(cmd_run(paths, cast(list[Arm], args.arms), args.pages))
            case "counters":
                counters(paths, args.fingerprint_v2, args.fingerprint_plain)
            case "issues":
                issues(paths, args.fingerprint_v2, args.pages)
    except TryoutError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
