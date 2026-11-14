"""The `fetch` step (ADR-0015, points 2 to 5): every published post of the public WordPress REST
API, stored locally as returned, and a committed manifest without title, text or author.

Modes:
- `first`: no post file exists locally; every page is listed.
- `incremental`: post files exist; only posts modified after the latest `modified_gmt` of the
  manifest minus a margin of one day (`MODIFIED_AFTER_MARGIN`) are listed (`modified_after`).
  The margin covers an unverified point: how WordPress compares the UTC value with
  `post_modified`, which is in the site's local time. It only lists a few unchanged posts again,
  which are counted `unchanged` and not rewritten. With an empty manifest (a first run that
  failed midway), there is no such date and every page is listed.
- `full` (`--full`): every page is listed again.

A post file is written when the post is new or its `modified_gmt` differs from the manifest's
(from the file's own `modified_gmt` when the post has a file but no manifest line, which happens
after a run that failed midway). The manifest, `categories.json` and the fetch log are written
only after the last page, so a failed run leaves them as they were.
"""

import asyncio
import hashlib
import logging
import os
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from dzdia_newsroom.llm.retry import RequestSpacer, retry_async
from evals.baseline_v1.jsonl import append_jsonl, read_jsonl, write_jsonl
from evals.baseline_v1.sample import USER_AGENT
from evals.dzdia_corpus import CorpusError
from evals.dzdia_corpus.paths import CorpusPaths
from evals.dzdia_corpus.records import (
    FetchMode,
    FetchRun,
    ManifestLine,
    WpPost,
    category_names,
    names_of,
    parse_categories,
    parse_post,
    post_ids,
    read_json,
    write_json_atomic,
)

BASE_URL = "https://dzdia.com/wp-json/wp/v2"
PER_PAGE = 100
TIMEOUT_S = 30.0
MAX_RPM = 30  # at least 2 s between the starts of two requests, retries included
MAX_RETRIES = 3  # on transport errors, HTTP 429 and 5xx
# Subtracted from the latest stored `modified_gmt` before it is sent as `modified_after`: how
# WordPress reads the offset against `post_modified` (site local time) is unverified, and a
# margin only lists a few unchanged posts again (counted, not rewritten), never misses one.
MODIFIED_AFTER_MARGIN = timedelta(days=1)

logger = logging.getLogger(__name__)

Outcome = Literal["new", "changed", "unchanged"]


class _RetryableStatus(Exception):
    def __init__(self, status: int, retry_after_s: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after_s = retry_after_s


def _retry_after(response: httpx.Response) -> float | None:
    """`Retry-After` in seconds; an HTTP-date or a missing header gives None."""
    value = response.headers.get("Retry-After", "").strip()
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, _RetryableStatus | httpx.TransportError)


def _retry_delay(exc: Exception) -> float | None:
    return exc.retry_after_s if isinstance(exc, _RetryableStatus) else None


class WpApi:
    """GET requests to the REST API: User-Agent, timeout, spacing, bounded retries, one INFO log
    line per attempt (path, page, status, latency, attempt; never the body). `requests` counts
    every request sent, retries included."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = BASE_URL,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._spacer = RequestSpacer(MAX_RPM, clock=clock, sleep=sleep)
        self.requests = 0

    async def get(self, path: str, params: Mapping[str, str | int]) -> httpx.Response:
        page = params.get("page", "-")
        attempt = 0

        async def call() -> httpx.Response:
            nonlocal attempt
            attempt += 1
            await self._spacer.wait()
            self.requests += 1
            start = time.perf_counter()
            try:
                response = await self._client.get(
                    self._base_url + path,
                    params=dict(params),
                    headers={"User-Agent": USER_AGENT},
                    timeout=TIMEOUT_S,
                )
            except httpx.TransportError as exc:
                self._log(path, page, type(exc).__name__, start, attempt)
                raise
            status = response.status_code
            self._log(path, page, status, start, attempt)
            if status == 429 or status >= 500:
                raise _RetryableStatus(status, _retry_after(response))
            if not 200 <= status < 300:
                raise CorpusError(f"GET {path} page {page}: HTTP {status}, not retried")
            return response

        try:
            return await retry_async(
                call,
                is_retryable=_is_retryable,
                retry_delay=_retry_delay,
                max_retries=MAX_RETRIES,
                sleep=self._sleep,
            )
        except (_RetryableStatus, httpx.TransportError) as exc:
            raise CorpusError(
                f"GET {path} page {page}: {str(exc) or type(exc).__name__} at attempt {attempt}, "
                "giving up"
            ) from exc

    @staticmethod
    def _log(path: str, page: object, status: object, start: float, attempt: int) -> None:
        latency_ms = round((time.perf_counter() - start) * 1000)
        logger.info(
            "GET %s page=%s status=%s latency_ms=%d attempt=%d",
            path,
            page,
            status,
            latency_ms,
            attempt,
            extra={
                "path": path,
                "page": page,
                "status": status,
                "latency_ms": latency_ms,
                "attempt": attempt,
            },
        )


def _total_pages(response: httpx.Response) -> int:
    value = response.headers.get("X-WP-TotalPages")
    if value is None or not value.strip().isdigit():
        raise CorpusError(f"{response.request.url.path}: missing or invalid X-WP-TotalPages")
    return int(value)


def _json_list(response: httpx.Response) -> list[Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise CorpusError(f"{response.request.url.path}: response is not JSON") from exc
    if not isinstance(data, list):
        raise CorpusError(f"{response.request.url.path}: response is not a JSON list")
    return data


def _as_utc(value: str) -> datetime:
    """A WordPress `*_gmt` date (no offset) read as UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CorpusError(f"Invalid date in the manifest: {value!r}") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _utc_z(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest(lines: Iterable[ManifestLine]) -> datetime | None:
    dates = [_as_utc(line.modified_gmt) for line in lines]
    return max(dates) if dates else None


def latest_modified(lines: Iterable[ManifestLine]) -> str | None:
    """The latest `modified_gmt` of the manifest as ISO 8601 UTC with a `Z`, or None if empty."""
    latest = _latest(lines)
    return _utc_z(latest) if latest is not None else None


def modified_after(lines: Iterable[ManifestLine]) -> str | None:
    """The `modified_after` value of an incremental run: the latest `modified_gmt` of the
    manifest minus `MODIFIED_AFTER_MARGIN`, as ISO 8601 UTC with a `Z`; None if empty."""
    latest = _latest(lines)
    return _utc_z(latest - MODIFIED_AFTER_MARGIN) if latest is not None else None


def manifest_line(post: WpPost, names: Mapping[int, str]) -> ManifestLine:
    content = post.content.rendered
    return ManifestLine(
        id=post.id,
        url=post.link,
        published_gmt=post.date_gmt,
        modified_gmt=post.modified_gmt,
        categories=names_of(post.categories, names),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        content_chars=len(content),
    )


def _store(paths: CorpusPaths, record: Any, post: WpPost, known: ManifestLine | None) -> Outcome:
    """Write the post file when needed; say whether the post was new, changed or unchanged.

    The reference `modified_gmt` is the manifest's; for a post with a file but no manifest line
    (a run that failed midway), it is the file's. A post without a local file is new.
    """
    path = paths.post_file(post.id)
    if not path.exists():
        write_json_atomic(path, record)
        return "new"
    stored: object
    if known is not None:
        stored = known.modified_gmt
    else:
        data = read_json(path)
        stored = data.get("modified_gmt") if isinstance(data, dict) else None
    if stored == post.modified_gmt:
        return "unchanged"
    write_json_atomic(path, record)
    return "changed"


def _write_manifest(paths: CorpusPaths, lines: Iterable[ManifestLine]) -> None:
    """Rewrite the whole manifest, sorted by id, through a temporary file."""
    tmp = paths.manifest.with_name(paths.manifest.name + ".tmp")
    ordered: list[ManifestLine] = sorted(lines, key=lambda line: line.id)
    write_jsonl(tmp, ordered)
    os.replace(tmp, paths.manifest)


async def fetch_corpus(
    paths: CorpusPaths,
    client: httpx.AsyncClient,
    *,
    full: bool = False,
    base_url: str = BASE_URL,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FetchRun:
    """One run of the `fetch` step; returns the line appended to the fetch log."""
    manifest = {line.id: line for line in read_jsonl(paths.manifest, ManifestLine)}
    mode: FetchMode = "full" if full else ("incremental" if post_ids(paths) else "first")
    api = WpApi(client, base_url=base_url, sleep=sleep, clock=clock)

    response = await api.get("/categories", {"per_page": PER_PAGE})
    if _total_pages(response) > 1:
        raise CorpusError("More than one page of categories: not handled")
    categories_raw = _json_list(response)
    names = category_names(parse_categories(categories_raw))

    params: dict[str, str | int] = {
        "per_page": PER_PAGE,
        "orderby": "id",
        "order": "asc",
        "status": "publish",
    }
    if mode == "incremental":
        after = modified_after(manifest.values())
        if after is None:
            logger.info("Incremental run with an empty manifest: listing every page")
        else:
            params["modified_after"] = after

    counts: Counter[Outcome] = Counter()
    listed: dict[int, ManifestLine] = {}
    page, total_pages = 1, 1
    while page <= total_pages:
        response = await api.get("/posts", {**params, "page": page})
        if page == 1:
            total_pages = _total_pages(response)
        for record in _json_list(response):
            post = parse_post(record)
            counts[_store(paths, record, post, manifest.get(post.id))] += 1
            listed[post.id] = manifest_line(post, names)
        page += 1

    merged = {**manifest, **listed}
    paths.dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(paths, merged.values())
    write_json_atomic(paths.categories, categories_raw)
    run = FetchRun(
        fetched_at=now(),
        mode=mode,
        requests=api.requests,
        posts_listed=len(listed),
        new=counts["new"],
        changed=counts["changed"],
        unchanged=counts["unchanged"],
        total_in_manifest=len(merged),
    )
    append_jsonl(paths.fetch_log, [run])
    return run


def fetch_lines(run: FetchRun) -> list[str]:
    """Counters only: never a title or text."""
    return [
        f"Mode {run.mode}: {run.requests} requests, {run.posts_listed} posts listed.",
        f"{run.new} new, {run.changed} changed, {run.unchanged} unchanged; "
        f"{run.total_in_manifest} posts in the manifest.",
    ]
