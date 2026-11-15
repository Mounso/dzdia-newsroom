"""Guarded download of a page that a journalist sent (ADR-0018, points 5 to 8 and 13).

The caller passes a client from `guarded_client(timeout=httpx.Timeout(PHASE_TIMEOUT_S))`: the guard
under it checks the address of every connection. This module checks every URL (the first one and
each redirect hop), follows redirects by hand, and bounds the time, the size and the type of what
it reads. The bot names itself and never works around a refusal; `robots.txt` is not read for a
page that a journalist asks for.
"""

import asyncio
import codecs
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import version

import httpx

from dzdia_newsroom.guardrails.ssrf import BlockedUrlError
from dzdia_newsroom.llm.retry import retry_async

__all__ = [
    "MAX_BODY_BYTES",
    "MAX_REDIRECTS",
    "PHASE_TIMEOUT_S",
    "TOTAL_DEADLINE_S",
    "UNSUPPORTED_HOSTS",
    "USER_AGENT",
    "BlockedUrlError",
    "FetchError",
    "FetchReason",
    "FetchedPage",
    "InvalidLinkError",
    "UnsupportedLinkError",
    "check_link",
    "fetch_html",
]

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 2 * 1024 * 1024
TOTAL_DEADLINE_S = 15.0
PHASE_TIMEOUT_S = 5.0
RETRY_DELAY_S = 1.0
USER_AGENT = f"Mozilla/5.0 (compatible; DzdiaNewsroomBot/{version('dzdia-newsroom')})"

# Video and social links get a dedicated answer, with no fetch (ADR-0018, point 8).
UNSUPPORTED_HOSTS = frozenset(
    {
        "instagram.com",
        "tiktok.com",
        "youtube.com",
        "youtu.be",
        "facebook.com",
        "fb.watch",
        "x.com",
        "twitter.com",
    }
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({80, 443})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({502, 503, 504})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_CHARSET_SNIFF_BYTES = 4096
_META_CHARSET = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)


class InvalidLinkError(Exception):
    """The link sent by the journalist is malformed or breaks the URL rule."""


class UnsupportedLinkError(Exception):
    """A video or social link: it is not fetched."""


class FetchReason(StrEnum):
    NOT_FOUND = "not_found"
    REFUSED = "refused"
    UNREACHABLE = "unreachable"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    TOO_LARGE = "too_large"
    NOT_HTML = "not_html"


class FetchError(Exception):
    def __init__(self, reason: FetchReason, status: int | None = None) -> None:
        super().__init__(reason.value if status is None else f"{reason.value} (HTTP {status})")
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    html: str


@dataclass
class _Stats:
    """What one fetch did, for its log line."""

    attempts: int = 0
    hops: int = 0
    status: int | None = None
    bytes: int = 0


def _is_unsupported_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == name or host.endswith(f".{name}") for name in UNSUPPORTED_HOSTS)


def _rule_violation(url: httpx.URL) -> str | None:
    """The URL rule of ADR-0018, point 5, applied to the first URL and to every hop."""
    if url.scheme not in _ALLOWED_SCHEMES:
        return "scheme is not http or https"
    if not url.host:
        return "no host"
    if url.userinfo:
        return "credentials in the URL"
    port = url.port if url.port is not None else _DEFAULT_PORTS[url.scheme]
    if port not in _ALLOWED_PORTS:
        return f"port {port} is not allowed"
    return None


def check_link(url: str) -> httpx.URL:
    """The check of the first URL, before any network call (ADR-0018, points 5 and 8; ADR-0019,
    point 4): raises UnsupportedLinkError for a video or social host, InvalidLinkError for a
    malformed link or one that breaks the URL rule. No network call. `fetch_html` calls it
    first."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise InvalidLinkError("the link is malformed") from exc
    if parsed.host and _is_unsupported_host(parsed.host):
        raise UnsupportedLinkError(f"host {parsed.host!r} is a video or social site")
    violation = _rule_violation(parsed)
    if violation is not None:
        raise InvalidLinkError(violation)
    return parsed


def _next_hop(current: httpx.URL, location: str) -> httpx.URL:
    try:
        target = current.join(location)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise BlockedUrlError(f"redirect from host {current.host!r} is malformed") from exc
    # Checked first: a short link landing on a social site gets the dedicated answer.
    if target.host and _is_unsupported_host(target.host):
        raise UnsupportedLinkError(f"redirect to host {target.host!r}, a video or social site")
    violation = _rule_violation(target)
    if violation is not None:
        raise BlockedUrlError(f"redirect from host {current.host!r} refused: {violation}")
    if current.scheme == "https" and target.scheme == "http":
        raise BlockedUrlError(f"redirect from host {current.host!r} refused: https to http")
    return target


def _check_status(status: int) -> None:
    if status == 200:
        return
    if status in (404, 410):
        raise FetchError(FetchReason.NOT_FOUND, status)
    if status in (401, 403, 429):
        raise FetchError(FetchReason.REFUSED, status)
    raise FetchError(FetchReason.UNREACHABLE, status)


def _decode(body: bytes, header_charset: str | None) -> str:
    """Header charset, else a `charset=` in the first bytes (meta tag), else UTF-8."""
    charset = header_charset
    if not charset:
        match = _META_CHARSET.search(body[:_CHARSET_SNIFF_BYTES])
        charset = match.group(1).decode("ascii") if match else None
    if charset:
        try:
            codecs.lookup(charset)
            return body.decode(charset, errors="replace")
        except LookupError:  # unknown codec, or a codec that is not a text encoding
            pass
    return body.decode("utf-8", errors="replace")


async def _read_body(response: httpx.Response) -> bytes:
    """The decompressed body, refused as soon as it passes MAX_BODY_BYTES."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise FetchError(FetchReason.TOO_LARGE, response.status_code)
    return bytes(body)


async def _attempt(client: httpx.AsyncClient, first: httpx.URL, stats: _Stats) -> FetchedPage:
    """One pass over the redirect chain, from the first URL."""
    stats.attempts += 1
    stats.hops = 0
    stats.status = None
    current = first
    while True:
        async with client.stream(
            "GET", current, headers={"User-Agent": USER_AGENT}, follow_redirects=False
        ) as response:
            stats.status = response.status_code
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(FetchReason.UNREACHABLE, response.status_code)
                if stats.hops >= MAX_REDIRECTS:
                    raise FetchError(FetchReason.TOO_MANY_REDIRECTS, response.status_code)
                current = _next_hop(current, location)
                stats.hops += 1
                continue
            _check_status(response.status_code)
            media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if media_type not in _HTML_TYPES:
                raise FetchError(FetchReason.NOT_HTML, response.status_code)
            body = await _read_body(response)
            stats.bytes = len(body)
            html = _decode(body, response.charset_encoding)
            return FetchedPage(url=str(first), final_url=str(response.url), html=html)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):  # connection errors and per-phase timeouts
        return True
    return isinstance(exc, FetchError) and exc.status in _RETRYABLE_STATUSES


def _outcome(exc: Exception) -> str:
    if isinstance(exc, FetchError):
        return exc.reason.value
    return {
        BlockedUrlError: "blocked",
        InvalidLinkError: "invalid_link",
        UnsupportedLinkError: "unsupported_link",
    }.get(type(exc), type(exc).__name__)


async def fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    deadline_s: float = TOTAL_DEADLINE_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FetchedPage:
    """Download one HTML page, following at most MAX_REDIRECTS redirects by hand.

    Raises InvalidLinkError, UnsupportedLinkError, BlockedUrlError or FetchError. One deadline
    covers every hop, the body and the retry.
    """
    start = time.perf_counter()
    stats = _Stats()
    host = ""
    try:
        first = check_link(url)
        host = first.host
        try:
            async with asyncio.timeout(deadline_s):
                page = await retry_async(
                    lambda: _attempt(client, first, stats),
                    is_retryable=_is_retryable,
                    max_retries=1,
                    base_delay_s=RETRY_DELAY_S,
                    sleep=sleep,
                )
        except TimeoutError as exc:
            raise FetchError(FetchReason.UNREACHABLE) from exc
        except httpx.HTTPError as exc:  # transport errors after the retry, bad encodings
            raise FetchError(FetchReason.UNREACHABLE) from exc
    except Exception as exc:
        _log(host, _outcome(exc), stats, start, level=logging.WARNING)
        raise
    _log(host, "ok", stats, start, level=logging.INFO)
    return page


def _log(host: str, outcome: str, stats: _Stats, start: float, *, level: int) -> None:
    # The host only: a full URL can carry tokens or personal data in its path or query.
    logger.log(
        level,
        "page fetch %s -> %s",
        host,
        outcome,
        extra={
            "host": host,
            "outcome": outcome,
            "status": stats.status,
            "bytes": stats.bytes,
            "hops": stats.hops,
            "attempts": stats.attempts,
            "latency_ms": int((time.perf_counter() - start) * 1000),
        },
    )
