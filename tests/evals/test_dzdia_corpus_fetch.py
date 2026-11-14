"""The `fetch` step of the Dzdia corpus (ADR-0015): modes, files, manifest, fetch log, retries,
spacing, failure atomicity. No network: `httpx.MockTransport` serves a fake WordPress API, and the
clock and sleep are fakes, so nothing waits."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.baseline_v1.jsonl import read_jsonl
from evals.baseline_v1.sample import USER_AGENT
from evals.dzdia_corpus import CorpusError
from evals.dzdia_corpus.fetch import (
    BASE_URL,
    MODIFIED_AFTER_MARGIN,
    fetch_corpus,
    fetch_lines,
    latest_modified,
    modified_after,
)
from evals.dzdia_corpus.paths import CorpusPaths
from evals.dzdia_corpus.records import FetchRun, ManifestLine

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
CATEGORIES = [
    {"id": 1, "name": "Musique", "count": 3, "slug": "musique"},
    {"id": 2, "name": "Arts &amp; Culture", "count": 1, "slug": "arts"},
]
MANIFEST_KEYS = {
    "id",
    "url",
    "published_gmt",
    "modified_gmt",
    "categories",
    "content_sha256",
    "content_chars",
}

Reply = httpx.Response | Exception


def _post(
    post_id: int, modified: str = "2026-11-01T10:00:00", categories: tuple[int, ...] = (1,)
) -> dict[str, Any]:
    return {
        "id": post_id,
        "link": f"https://dzdia.com/article-{post_id}/",
        "date_gmt": "2026-10-01T09:00:00",
        "modified_gmt": modified,
        "title": {"rendered": f"Titre secret {post_id}"},
        "content": {"rendered": f"<p>Texte secret é {post_id}</p>\n", "protected": False},
        "excerpt": {"rendered": "<p>Résumé</p>"},
        "author": 7,
        "tags": [3, 4],
        "categories": list(categories),
    }


class FakeTime:
    """A clock that moves only when the code sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeWp:
    """The REST API: posts in pages of `page_size`, filtered by `modified_after` (strictly
    after, as WordPress does), returned in the given order. `replies[(path, page)]` are served
    first, one per request, before the normal answer."""

    def __init__(self, posts: list[dict[str, Any]], time: FakeTime, page_size: int = 2) -> None:
        self.posts = posts
        self.time = time
        self.page_size = page_size
        self.categories: list[dict[str, Any]] = CATEGORIES
        self.category_pages = 1
        self.replies: dict[tuple[str, int], list[Reply]] = {}
        self.requests: list[httpx.Request] = []
        self.starts: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.starts.append(self.time.now)
        path = request.url.path.removeprefix("/wp-json/wp/v2")
        page = int(request.url.params.get("page", "1"))
        queued = self.replies.get((path, page))
        if queued:
            reply = queued.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        if path == "/categories":
            headers = {"X-WP-TotalPages": str(self.category_pages)}
            return httpx.Response(200, json=self.categories, headers=headers)
        posts = self.posts
        after = request.url.params.get("modified_after")
        if after is not None:
            limit = datetime.fromisoformat(after)
            posts = [
                p
                for p in posts
                if datetime.fromisoformat(p["modified_gmt"]).replace(tzinfo=UTC) > limit
            ]
        pages = [posts[i : i + self.page_size] for i in range(0, len(posts), self.page_size)]
        body = pages[page - 1] if pages else []
        return httpx.Response(200, json=body, headers={"X-WP-TotalPages": str(len(pages))})

    def post_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/posts")]


def _paths(tmp_path: Path) -> CorpusPaths:
    return CorpusPaths(dataset_dir=tmp_path / "datasets", data_dir=tmp_path / "data")


async def _fetch(paths: CorpusPaths, wp: FakeWp, *, full: bool = False) -> FetchRun:
    async with httpx.AsyncClient(transport=httpx.MockTransport(wp)) as client:
        return await fetch_corpus(
            paths, client, full=full, sleep=wp.time.sleep, clock=wp.time.clock, now=lambda: NOW
        )


def _manifest(paths: CorpusPaths) -> list[ManifestLine]:
    return read_jsonl(paths.manifest, ManifestLine)


def _fetches(paths: CorpusPaths) -> list[FetchRun]:
    return read_jsonl(paths.fetch_log, FetchRun)


def _snapshot(paths: CorpusPaths) -> dict[str, tuple[bytes, int]]:
    """Bytes and mtime of every local post file."""
    return {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(paths.posts_dir.iterdir())
    }


async def test_first_run_over_two_pages_writes_posts_manifest_log_and_categories(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    posts = [_post(3, "2026-11-03T08:00:00"), _post(1), _post(2, categories=(2, 1))]
    wp = FakeWp(posts, FakeTime())

    run = await _fetch(paths, wp)

    assert run == FetchRun(
        fetched_at=NOW,
        mode="first",
        requests=3,
        posts_listed=3,
        new=3,
        changed=0,
        unchanged=0,
        total_in_manifest=3,
    )
    assert _fetches(paths) == [run]
    for post in posts:
        stored = paths.post_file(post["id"]).read_bytes()
        assert json.loads(stored) == post  # the record as returned
        assert b"\r\n" not in stored and "é".encode() in stored  # \n newlines, UTF-8 kept
        assert stored.decode().index('"author"') < stored.decode().index('"title"')  # sorted
    assert json.loads(paths.categories.read_text(encoding="utf-8")) == CATEGORIES

    lines = _manifest(paths)
    assert [line.id for line in lines] == [1, 2, 3]
    raw = [json.loads(line) for line in paths.manifest.read_text(encoding="utf-8").splitlines()]
    assert all(set(r) == MANIFEST_KEYS for r in raw)  # no title, author, text or tags
    assert "secret" not in paths.manifest.read_text(encoding="utf-8")
    second = lines[1]
    content = posts[2]["content"]["rendered"]
    assert second.url == "https://dzdia.com/article-2/"
    assert second.published_gmt == "2026-10-01T09:00:00"
    assert second.categories == ["Arts & Culture", "Musique"]  # names, unescaped, sorted
    assert second.content_chars == len(content)
    assert len(second.content_sha256) == 64

    first_page = wp.post_requests()[0].url.params
    assert dict(first_page) == {
        "per_page": "100",
        "orderby": "id",
        "order": "asc",
        "status": "publish",
        "page": "1",
    }
    assert [r.url.params["page"] for r in wp.post_requests()] == ["1", "2"]
    assert all(str(r.url).startswith(BASE_URL) for r in wp.requests)
    assert all(r.headers["User-Agent"] == USER_AGENT for r in wp.requests)
    assert wp.requests[0].url.path.endswith("/categories")
    assert wp.requests[0].url.params["per_page"] == "100"


async def test_requests_start_at_least_two_seconds_apart(tmp_path: Path) -> None:
    wp = FakeWp([_post(i) for i in range(1, 6)], FakeTime())

    await _fetch(_paths(tmp_path), wp)

    assert len(wp.starts) == 4  # categories + 3 pages
    assert all(b - a >= 2.0 for a, b in zip(wp.starts, wp.starts[1:], strict=False))
    assert wp.time.sleeps and all(s >= 2.0 for s in wp.time.sleeps)  # no real sleeping


async def test_incremental_run_lists_since_latest_modified_and_writes_only_new_and_changed(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    time = FakeTime()
    posts = [_post(1), _post(2), _post(3, "2026-11-03T08:00:00")]
    await _fetch(paths, FakeWp(posts, time))
    before = _snapshot(paths)

    edited = _post(2, "2026-11-05T10:00:00")
    edited["content"]["rendered"] = "<p>Texte corrigé</p>"
    wp = FakeWp([posts[0], edited, posts[2], _post(4, "2026-11-06T10:00:00")], time)
    run = await _fetch(paths, wp)

    # Latest stored modified_gmt (post 3, 2026-11-03 08:00) minus the one-day margin: post 3 is
    # inside the window, listed again and counted unchanged; post 1 (2026-11-01) is not listed.
    assert wp.post_requests()[0].url.params["modified_after"] == "2026-11-02T08:00:00Z"
    assert (run.mode, run.posts_listed, run.new, run.changed, run.unchanged) == (
        "incremental",
        3,
        1,
        1,
        1,
    )
    assert run.total_in_manifest == 4 and run.requests == 3  # categories + 2 pages
    after = _snapshot(paths)
    assert after["1.json"] == before["1.json"] and after["3.json"] == before["3.json"]
    assert json.loads(paths.post_file(2).read_bytes()) == edited
    lines = {line.id: line for line in _manifest(paths)}
    assert sorted(lines) == [1, 2, 3, 4]
    assert lines[2].modified_gmt == "2026-11-05T10:00:00"
    assert lines[2].content_chars == len("<p>Texte corrigé</p>")
    assert len(_fetches(paths)) == 2


async def test_full_run_leaves_an_unchanged_post_untouched(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    posts = [_post(1), _post(2), _post(3)]
    await _fetch(paths, FakeWp(posts, FakeTime()))
    before = _snapshot(paths)
    manifest_before = paths.manifest.read_bytes()

    wp = FakeWp(posts, FakeTime())
    run = await _fetch(paths, wp, full=True)

    assert (run.mode, run.new, run.changed, run.unchanged) == ("full", 0, 0, 3)
    assert all("modified_after" not in r.url.params for r in wp.post_requests())
    assert _snapshot(paths) == before
    assert paths.manifest.read_bytes() == manifest_before


async def test_post_absent_from_a_full_listing_keeps_its_file_and_manifest_line(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    await _fetch(paths, FakeWp([_post(1), _post(2), _post(3)], FakeTime()))

    run = await _fetch(paths, FakeWp([_post(1), _post(3)], FakeTime()), full=True)

    assert (run.posts_listed, run.total_in_manifest) == (2, 3)
    assert paths.post_file(2).exists()
    assert [line.id for line in _manifest(paths)] == [1, 2, 3]


async def test_503_is_retried_and_counted(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    wp = FakeWp([_post(1)], FakeTime())
    wp.replies[("/posts", 1)] = [httpx.Response(503), httpx.Response(502)]

    run = await _fetch(paths, wp)

    assert run.requests == 4  # categories + 2 failures + success
    assert run.new == 1
    assert all(b - a >= 2.0 for a, b in zip(wp.starts, wp.starts[1:], strict=False))


async def test_transport_error_is_retried(tmp_path: Path) -> None:
    wp = FakeWp([_post(1)], FakeTime())
    wp.replies[("/posts", 1)] = [httpx.ConnectError("refused")]

    run = await _fetch(_paths(tmp_path), wp)

    assert run.requests == 3


async def test_429_waits_for_retry_after(tmp_path: Path) -> None:
    wp = FakeWp([_post(1)], FakeTime())
    wp.replies[("/categories", 1)] = [httpx.Response(429, headers={"Retry-After": "7"})]

    run = await _fetch(_paths(tmp_path), wp)

    assert run.requests == 3
    assert any(s >= 7.0 for s in wp.time.sleeps)
    assert wp.starts[1] - wp.starts[0] >= 7.0


async def test_retries_are_bounded_then_fail_with_the_package_error(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    wp = FakeWp([_post(1)], FakeTime())
    wp.replies[("/posts", 1)] = [httpx.Response(500) for _ in range(10)]

    with pytest.raises(CorpusError, match="HTTP 500"):
        await _fetch(paths, wp)

    assert len(wp.post_requests()) == 4  # one try and three retries
    assert not paths.manifest.exists() and not paths.fetch_log.exists()


async def test_404_fails_at_once(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    wp = FakeWp([_post(1)], FakeTime())
    wp.replies[("/posts", 1)] = [httpx.Response(404)]

    with pytest.raises(CorpusError, match="HTTP 404"):
        await _fetch(paths, wp)

    assert len(wp.requests) == 2  # categories + the 404, not retried
    assert not paths.manifest.exists() and not paths.fetch_log.exists()
    assert not paths.categories.exists()


async def test_failure_on_page_two_leaves_manifest_and_log_unchanged(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    await _fetch(paths, FakeWp([_post(1), _post(2), _post(3)], FakeTime()))
    manifest = paths.manifest.read_bytes()
    log = paths.fetch_log.read_bytes()

    posts = [_post(1, "2026-11-08T10:00:00"), _post(2), _post(3, "2026-11-08T10:00:00")]
    wp = FakeWp(posts, FakeTime())
    wp.replies[("/posts", 2)] = [httpx.Response(403)]
    with pytest.raises(CorpusError):
        await _fetch(paths, wp, full=True)

    assert paths.manifest.read_bytes() == manifest
    assert paths.fetch_log.read_bytes() == log
    # The page-1 file already written stays; the next run reconciles it against the manifest.
    assert json.loads(paths.post_file(1).read_bytes())["modified_gmt"] == "2026-11-08T10:00:00"
    run = await _fetch(paths, FakeWp(posts, FakeTime()), full=True)
    assert (run.new, run.changed, run.unchanged) == (0, 2, 1)
    assert {line.id: line.modified_gmt for line in _manifest(paths)}[3] == "2026-11-08T10:00:00"


async def test_after_a_failed_first_run_the_next_run_reconciles_with_the_files(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    posts = [_post(1), _post(2), _post(3)]
    wp = FakeWp(posts, FakeTime())
    wp.replies[("/posts", 2)] = [httpx.Response(404)]
    with pytest.raises(CorpusError):
        await _fetch(paths, wp)
    assert not paths.manifest.exists()
    assert paths.post_file(1).exists() and paths.post_file(2).exists()
    written = _snapshot(paths)

    edited = _post(2, "2026-11-09T10:00:00")
    wp = FakeWp([posts[0], edited, posts[2]], FakeTime())
    run = await _fetch(paths, wp)

    # Files exist, so the mode is incremental; with no manifest line there is no date to
    # filter on, and every page is listed.
    assert run.mode == "incremental"
    assert all("modified_after" not in r.url.params for r in wp.post_requests())
    assert (run.new, run.changed, run.unchanged, run.total_in_manifest) == (1, 1, 1, 3)
    assert _snapshot(paths)["1.json"] == written["1.json"]
    assert json.loads(paths.post_file(2).read_bytes()) == edited


async def test_categories_over_more_than_one_page_fail(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    wp = FakeWp([_post(1)], FakeTime())
    wp.category_pages = 2

    with pytest.raises(CorpusError, match="categories"):
        await _fetch(paths, wp)

    assert len(wp.requests) == 1
    assert not paths.posts_dir.exists()


async def test_unknown_category_id_is_kept_as_hash_id(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    await _fetch(paths, FakeWp([_post(1, categories=(99, 1))], FakeTime()))

    assert _manifest(paths)[0].categories == ["#99", "Musique"]


async def test_screen_lines_hold_counters_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    run = await _fetch(paths, FakeWp([_post(1), _post(2)], FakeTime()))

    text = "\n".join(fetch_lines(run))
    assert "Mode first" in text and "2 new" in text and "2 posts in the manifest" in text
    assert "secret" not in text and "Texte" not in text


def _line(post_id: int, modified: str) -> ManifestLine:
    return ManifestLine(
        id=post_id,
        url="u",
        published_gmt=modified,
        modified_gmt=modified,
        categories=[],
        content_sha256="h",
        content_chars=0,
    )


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        ([], None),
        (
            ["2026-11-03T08:00:00", "2026-11-12T23:59:01", "2026-01-01T00:00:00"],
            "2026-11-12T23:59:01Z",
        ),
    ],
)
def test_latest_modified_is_utc_with_z(dates: list[str], expected: str | None) -> None:
    assert latest_modified([_line(i, d) for i, d in enumerate(dates)]) == expected


def test_modified_after_is_the_latest_minus_the_margin() -> None:
    lines = [_line(1, "2026-11-01T10:00:00"), _line(2, "2026-03-01T00:30:00")]
    assert timedelta(days=1) == MODIFIED_AFTER_MARGIN
    assert latest_modified(lines) == "2026-11-01T10:00:00Z"  # the true latest value
    assert modified_after(lines) == "2026-10-31T10:00:00Z"
    assert modified_after([_line(1, "2026-03-01T00:30:00")]) == "2026-02-28T00:30:00Z"
    assert modified_after([]) is None
