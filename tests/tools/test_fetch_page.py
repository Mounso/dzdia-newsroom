"""Guarded page download (ADR-0018, points 5 to 8 and 13). Every test runs on httpx.MockTransport:
no real network."""

import asyncio
import gzip
import importlib.util
import inspect
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest

from dzdia_newsroom.tools import fetch_page
from dzdia_newsroom.tools.fetch_page import (
    MAX_BODY_BYTES,
    MAX_REDIRECTS,
    USER_AGENT,
    BlockedUrlError,
    FetchedPage,
    FetchError,
    FetchReason,
    InvalidLinkError,
    UnsupportedLinkError,
    check_link,
    fetch_html,
)

HTML = "<html><body><p>Bonjour Oran</p></body></html>"
HTML_TYPE = {"content-type": "text/html; charset=utf-8"}

type Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


class Recorder:
    """A MockTransport handler that records requests and answers from a list or a function."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.handler(request)
        if not isinstance(response, httpx.Response):
            response = await response
        return response


def _sequence(*responses: httpx.Response | Exception) -> Handler:
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def _ok(html: str = HTML, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, headers=headers if headers is not None else HTML_TYPE, text=html)


def _redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def _fetch(
    handler: Handler, url: str = "https://example.org/article", **kwargs: float
) -> tuple[FetchedPage, Recorder, SleepRecorder]:
    recorder = Recorder(handler)
    sleep = SleepRecorder()
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as client:
        page = await fetch_html(client, url, sleep=sleep, **kwargs)
    return page, recorder, sleep


async def _fetch_error[E: Exception](
    expected: type[E], handler: Handler, url: str = "https://example.org/article"
) -> tuple[E, Recorder, SleepRecorder]:
    recorder = Recorder(handler)
    sleep = SleepRecorder()
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as client:
        with pytest.raises(expected) as excinfo:
            await fetch_html(client, url, sleep=sleep)
    return excinfo.value, recorder, sleep


# --- success ---


async def test_a_plain_html_page_is_returned_with_the_bot_user_agent() -> None:
    page, recorder, sleep = await _fetch(_sequence(_ok()))
    assert page == FetchedPage(
        url="https://example.org/article", final_url="https://example.org/article", html=HTML
    )
    [request] = recorder.requests
    assert request.headers["user-agent"] == USER_AGENT
    assert USER_AGENT.startswith("Mozilla/5.0 (compatible; DzdiaNewsroomBot/")
    assert sleep.delays == []


async def test_a_chain_of_five_redirects_is_followed() -> None:
    chain = [
        _redirect(f"https://example.org/{i}", status)
        for i, status in enumerate([301, 302, 303, 307, 308], start=1)
    ]
    page, recorder, _ = await _fetch(_sequence(*chain, _ok()))
    assert MAX_REDIRECTS == 5
    assert len(recorder.requests) == 6
    assert page.final_url == "https://example.org/5"
    assert page.url == "https://example.org/article"


async def test_a_sixth_redirect_is_too_many() -> None:
    chain = [_redirect(f"https://example.org/{i}") for i in range(1, 7)]
    error, recorder, _ = await _fetch_error(FetchError, _sequence(*chain, _ok()))
    assert error.reason is FetchReason.TOO_MANY_REDIRECTS
    assert len(recorder.requests) == 6


async def test_a_relative_location_is_resolved_against_the_current_url() -> None:
    page, recorder, _ = await _fetch(
        _sequence(_redirect("../other?x=1"), _ok()), url="https://example.org/a/b/page"
    )
    assert str(recorder.requests[1].url) == "https://example.org/a/other?x=1"
    assert page.final_url == "https://example.org/a/other?x=1"


async def test_a_redirect_without_location_is_unreachable() -> None:
    error, _, _ = await _fetch_error(FetchError, _sequence(httpx.Response(302)))
    assert error.reason is FetchReason.UNREACHABLE and error.status == 302


@pytest.mark.parametrize(
    "location",
    [
        "ftp://example.org/file",
        "https://example.org:8080/",
        "https://user:secret@example.org/",
        "http://example.org/downgrade",  # https -> http
    ],
)
async def test_a_redirect_breaking_the_url_rule_is_blocked(location: str) -> None:
    _, recorder, _ = await _fetch_error(BlockedUrlError, _sequence(_redirect(location), _ok()))
    assert len(recorder.requests) == 1


async def test_a_redirect_from_http_to_https_is_allowed() -> None:
    page, _, _ = await _fetch(
        _sequence(_redirect("https://example.org/"), _ok()), url="http://example.org/"
    )
    assert page.final_url == "https://example.org/"


# --- first URL ---


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.org/file",
        "https://example.org:8080/",
        "https://user:secret@example.org/",
        "https:///path-only",
        "example.org/no-scheme",
        "http://[::1/",
    ],
)
async def test_an_invalid_first_url_is_refused_before_any_request(url: str) -> None:
    _, recorder, _ = await _fetch_error(InvalidLinkError, _sequence(_ok()), url=url)
    assert recorder.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://instagram.com/p/abc",
        "https://www.instagram.com/reel/abc",
        "https://tiktok.com/@a/video/1",
        "https://vm.tiktok.com/xyz",
        "https://youtube.com/watch?v=1",
        "https://m.youtube.com/watch?v=1",
        "https://youtu.be/1",
        "https://facebook.com/post/1",
        "https://fb.watch/abc",
        "https://x.com/a/status/1",
        "https://twitter.com/a/status/1",
        "https://WWW.Instagram.COM/p/abc",
    ],
)
async def test_video_and_social_links_are_unsupported_with_no_request(url: str) -> None:
    _, recorder, _ = await _fetch_error(UnsupportedLinkError, _sequence(_ok()), url=url)
    assert recorder.requests == []


def test_check_link_is_the_public_pre_network_check() -> None:
    # Synchronous and given no client: it cannot make a request (ADR-0019, point 4).
    assert "check_link" in fetch_page.__all__
    assert not inspect.iscoroutinefunction(check_link)
    assert list(inspect.signature(check_link).parameters) == ["url"]
    url = check_link("https://example.org/article?id=1")
    assert isinstance(url, httpx.URL) and url.host == "example.org"
    with pytest.raises(InvalidLinkError):
        check_link("https://example.org:8080/")
    with pytest.raises(UnsupportedLinkError):
        check_link("https://youtu.be/1")


async def test_a_short_link_landing_on_a_social_site_is_unsupported() -> None:
    _, recorder, _ = await _fetch_error(
        UnsupportedLinkError,
        _sequence(_redirect("https://www.instagram.com/reel/abc"), _ok()),
        url="https://bit.ly/abc",
    )
    assert len(recorder.requests) == 1


async def test_a_host_that_only_ends_with_a_social_name_is_fetched() -> None:
    page, _, _ = await _fetch(_sequence(_ok()), url="https://notinstagram.com/article")
    assert page.html == HTML


# --- statuses and retries ---


@pytest.mark.parametrize("status", [404, 410])
async def test_a_missing_page_is_not_found(status: int) -> None:
    error, recorder, _ = await _fetch_error(FetchError, _sequence(httpx.Response(status)))
    assert error.reason is FetchReason.NOT_FOUND and error.status == status
    assert len(recorder.requests) == 1


@pytest.mark.parametrize("status", [401, 403, 429])
async def test_a_refusal_is_never_retried(status: int) -> None:
    error, recorder, sleep = await _fetch_error(FetchError, _sequence(httpx.Response(status)))
    assert error.reason is FetchReason.REFUSED and error.status == status
    assert len(recorder.requests) == 1 and sleep.delays == []


@pytest.mark.parametrize("status", [500, 418])
async def test_other_statuses_are_unreachable_without_retry(status: int) -> None:
    error, recorder, _ = await _fetch_error(FetchError, _sequence(httpx.Response(status)))
    assert error.reason is FetchReason.UNREACHABLE and error.status == status
    assert len(recorder.requests) == 1


async def test_a_503_then_a_200_succeeds_after_one_sleep() -> None:
    page, recorder, sleep = await _fetch(_sequence(httpx.Response(503), _ok()))
    assert page.html == HTML
    assert len(recorder.requests) == 2
    assert len(sleep.delays) == 1 and 1.0 <= sleep.delays[0] <= 2.0


async def test_two_503_answers_are_unreachable() -> None:
    error, recorder, sleep = await _fetch_error(
        FetchError, _sequence(httpx.Response(503), httpx.Response(503), _ok())
    )
    assert error.reason is FetchReason.UNREACHABLE and error.status == 503
    assert len(recorder.requests) == 2 and len(sleep.delays) == 1


async def test_a_connection_error_then_a_200_succeeds() -> None:
    page, recorder, _ = await _fetch(_sequence(httpx.ConnectError("refused"), _ok()))
    assert page.html == HTML and len(recorder.requests) == 2


async def test_two_connection_errors_are_unreachable() -> None:
    error, recorder, _ = await _fetch_error(
        FetchError, _sequence(httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), _ok())
    )
    assert error.reason is FetchReason.UNREACHABLE and error.status is None
    assert len(recorder.requests) == 2


async def test_a_guard_refusal_crosses_unchanged_and_is_not_retried() -> None:
    refusal = BlockedUrlError("host 'internal.example' is not allowed")
    error, recorder, sleep = await _fetch_error(BlockedUrlError, _sequence(refusal, _ok()))
    assert error is refusal
    assert len(recorder.requests) == 1 and sleep.delays == []


# --- content type and size ---


@pytest.mark.parametrize("headers", [{"content-type": "application/pdf"}, {}])
async def test_a_page_that_is_not_html_is_refused(headers: dict[str, str]) -> None:
    response = httpx.Response(200, headers=headers, content=b"%PDF-1.7")
    error, recorder, _ = await _fetch_error(FetchError, _sequence(response))
    assert error.reason is FetchReason.NOT_HTML
    assert len(recorder.requests) == 1


async def test_xhtml_is_accepted() -> None:
    page, _, _ = await _fetch(
        _sequence(_ok(headers={"content-type": "Application/XHTML+XML; charset=utf-8"}))
    )
    assert page.html == HTML


async def test_the_size_limit_counts_decompressed_bytes() -> None:
    compressed = gzip.compress(b"a" * (MAX_BODY_BYTES + 1))
    assert len(compressed) < 100_000  # small on the wire
    response = httpx.Response(
        200, headers={**HTML_TYPE, "content-encoding": "gzip"}, content=compressed
    )
    error, _, _ = await _fetch_error(FetchError, _sequence(response))
    assert error.reason is FetchReason.TOO_LARGE


async def test_a_body_just_under_the_limit_passes() -> None:
    body = "a" * MAX_BODY_BYTES
    page, _, _ = await _fetch(_sequence(_ok(body)))
    assert len(page.html) == MAX_BODY_BYTES


async def test_reading_stops_once_the_limit_is_passed() -> None:
    chunk = b"a" * (512 * 1024)
    sent: list[int] = []

    async def stream() -> AsyncIterator[bytes]:
        for i in range(20):  # 10 MB in all
            sent.append(i)
            yield chunk

    response = httpx.Response(200, headers=HTML_TYPE, content=stream())
    error, _, _ = await _fetch_error(FetchError, _sequence(response))
    assert error.reason is FetchReason.TOO_LARGE
    assert len(sent) == 5  # 2.5 MB read, the rest never pulled


async def test_no_decoder_beyond_gzip_and_deflate_is_installed() -> None:
    # ADR-0018, point 6: the 2 MB limit counts decompressed bytes, but httpx expands one
    # compressed read (up to 64 KiB) before it is counted. With gzip and deflate that momentary
    # expansion stays bounded (about a thousand to one). A brotli or zstd decoder, pulled in later
    # by another dependency, would remove that bound silently. If this test fails, revisit
    # ADR-0018, point 6 before changing the test.
    for module in ("brotli", "brotlicffi", "zstandard"):
        assert importlib.util.find_spec(module) is None, f"{module} is installed"
    async with httpx.AsyncClient() as client:
        assert client.headers["accept-encoding"] == "gzip, deflate"


async def test_the_total_deadline_covers_the_whole_fetch() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return _ok()

    recorder = Recorder(slow)
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as client:
        with pytest.raises(FetchError) as excinfo:
            await fetch_html(client, "https://example.org/", deadline_s=0.05)
    assert excinfo.value.reason is FetchReason.UNREACHABLE


# --- decoding ---


async def test_the_header_charset_decodes_the_body() -> None:
    body = "<html><body>Béjaïa</body></html>".encode("iso-8859-1")
    response = httpx.Response(
        200, headers={"content-type": "text/html; charset=ISO-8859-1"}, content=body
    )
    page, _, _ = await _fetch(_sequence(response))
    assert "Béjaïa" in page.html


async def test_a_meta_charset_decodes_the_body() -> None:
    body = (
        '<html><head><meta charset="iso-8859-1"></head><body>Béjaïa, Sétif</body></html>'
    ).encode("iso-8859-1")
    response = httpx.Response(200, headers={"content-type": "text/html"}, content=body)
    page, _, _ = await _fetch(_sequence(response))
    assert "Béjaïa, Sétif" in page.html


async def test_utf8_is_the_default() -> None:
    body = "<html><body>Béjaïa</body></html>".encode()
    response = httpx.Response(200, headers={"content-type": "text/html"}, content=body)
    page, _, _ = await _fetch(_sequence(response))
    assert "Béjaïa" in page.html


@pytest.mark.parametrize("charset", ["no-such-codec", "rot13"])
async def test_an_unknown_codec_falls_back_to_utf8(charset: str) -> None:
    body = "<html><body>Béjaïa</body></html>".encode()
    response = httpx.Response(
        200, headers={"content-type": f"text/html; charset={charset}"}, content=body
    )
    page, _, _ = await _fetch(_sequence(response))
    assert "Béjaïa" in page.html


# --- logging ---


async def test_one_log_line_per_fetch_with_the_host_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="dzdia_newsroom.tools.fetch_page")
    await _fetch(_sequence(httpx.Response(503), _ok()), url="https://example.org/secret-path?q=1")
    [record] = caplog.records
    assert record.host == "example.org"  # type: ignore[attr-defined]
    assert record.outcome == "ok"  # type: ignore[attr-defined]
    assert record.status == 200  # type: ignore[attr-defined]
    assert record.bytes == len(HTML)  # type: ignore[attr-defined]
    assert record.hops == 0  # type: ignore[attr-defined]
    assert record.attempts == 2  # type: ignore[attr-defined]
    assert isinstance(record.latency_ms, int)  # type: ignore[attr-defined]
    assert "secret-path" not in record.getMessage()
    assert all("secret-path" not in str(value) for value in vars(record).values())


async def test_a_failure_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="dzdia_newsroom.tools.fetch_page")
    await _fetch_error(FetchError, _sequence(httpx.Response(404)))
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.outcome == "not_found"  # type: ignore[attr-defined]
    assert record.status == 404  # type: ignore[attr-defined]
