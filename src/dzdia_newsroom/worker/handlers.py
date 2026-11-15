"""Job handlers, keyed by job kind.

`inbound_message` (ADR-0019) runs the whole slice in one job, as a thin adapter over
`worker/replies.py` (what to say) and `tools/draft.py` (the pipeline): route the message,
acknowledge it on the first attempt, compose a draft under a deadline, send one reply. Nothing is
stored: a job that runs again does everything again, and a replay can send a second draft.

Errors (ADR-0018, point 13; ADR-0019, point 6):
- a typed error of the link or the site family, `GenerationFailedError` and the expired deadline
  send their message as a reply, then become `PermanentJobError`;
- any other error is left to the queue's retries; on the last attempt the "inside" message is
  sent first.

Channel errors are translated into the queue's terms: `PermanentChannelError` becomes
`PermanentJobError`, and a `ChannelError` with a `retry_after_s` becomes `RetryLaterJobError`.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import NoReturn

import httpx
from pydantic import ValidationError

from dzdia_newsroom.channels.base import (
    INBOUND_MESSAGE_KIND,
    Channel,
    ChannelError,
    InboundMessage,
    PermanentChannelError,
)
from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.guardrails.ssrf import BlockedUrlError
from dzdia_newsroom.llm import StructuredGenerator
from dzdia_newsroom.tools.article import PROMPT_VERSION, GenerationFailedError
from dzdia_newsroom.tools.draft import draft_from_link, draft_from_text
from dzdia_newsroom.tools.fetch_page import (
    FetchError,
    FetchReason,
    InvalidLinkError,
    UnsupportedLinkError,
    check_link,
)
from dzdia_newsroom.tools.page_text import NoReadableTextError
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.queue import Handler, PermanentJobError, RetryLaterJobError
from dzdia_newsroom.worker.replies import RouteKind, render_reply, route_message, warnings_for

logger = logging.getLogger(__name__)

# Covers the fetch and the generation of one job, never a send (ADR-0019, point 5).
DEFAULT_DEADLINE_S = 180.0
_MAX_ERROR_CHARS = 300


@dataclass
class _JobLog:
    """The fields of the single log line of a job (ADR-0019, point 11). Never the message
    text, the path or query of the link, or the article."""

    route: str | None = None
    outcome: str = "ok"
    host: str | None = None
    source_chars: int | None = None
    evidence_count: int | None = None
    model_calls: int = 0
    removed_quotes: int | None = None
    warnings: list[str] = field(default_factory=list)


def _describe(exc: BaseException) -> str:
    """The error type and its own short message: never the message text or the full link."""
    return f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]


def _error_text(exc: Exception) -> str | None:
    """The message of a typed error (ADR-0018, point 13), or None for any other error."""
    if isinstance(exc, InvalidLinkError | BlockedUrlError):
        return replies.LINK_ERROR
    if isinstance(exc, FetchError):
        return replies.LINK_ERROR if exc.reason is FetchReason.NOT_FOUND else replies.SITE_ERROR
    if isinstance(exc, UnsupportedLinkError):
        return replies.UNSUPPORTED_LINK_ERROR
    if isinstance(exc, NoReadableTextError):
        return replies.SITE_ERROR
    if isinstance(exc, GenerationFailedError):
        return replies.INSIDE_ERROR
    return None


def _host_of(link: str) -> str | None:
    try:
        return httpx.URL(link).host or None
    except (httpx.InvalidURL, TypeError, ValueError):
        return None


def build_handlers(
    channel: Channel,
    http: httpx.AsyncClient,
    llm: StructuredGenerator,
    *,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> dict[str, Handler]:
    """`http` must be the guarded client (`guarded_client`): it fetches the journalists' links."""

    async def send(message: InboundMessage, text: str) -> None:
        """Send `text` as a reply to the journalist's message, channel errors translated."""
        try:
            await channel.send_text(message.chat_id, text, reply_to=message.message_id)
        except PermanentChannelError as exc:
            raise PermanentJobError(str(exc)) from exc
        except ChannelError as exc:
            if exc.retry_after_s is None:
                raise  # the queue's own backoff applies
            raise RetryLaterJobError(str(exc), delay_s=exc.retry_after_s) from exc

    async def fail(
        message: InboundMessage, text: str, exc: Exception, description: str | None = None
    ) -> NoReturn:
        await send(message, text)
        raise PermanentJobError(description or _describe(exc)) from exc

    async def send_inside_text_quietly(message: InboundMessage) -> None:
        """The last attempt's message: a failure to send it must not hide the original error."""
        try:
            await channel.send_text(
                message.chat_id, replies.INSIDE_ERROR, reply_to=message.message_id
            )
        except ChannelError as exc:
            logger.warning(
                "could not send the inside text on the last attempt (%s)", _describe(exc)
            )

    async def run(job: ClaimedJob, log: _JobLog) -> None:
        try:
            message = InboundMessage.model_validate(job.payload)
        except ValidationError as exc:
            log.outcome = "ValidationError"
            # Only the error count: pydantic's message would copy payload values (user text)
            # into `jobs.last_error`.
            raise PermanentJobError(f"invalid payload ({exc.error_count()} errors)") from None

        route = route_message(message)
        log.route = route.kind.value
        if route.kind is RouteKind.SILENT:
            return
        if route.kind is RouteKind.HELP:
            await send(message, replies.HELP_TEXT)
            return

        link = route.link
        if link is not None:
            log.host = _host_of(link)
            try:
                check_link(link)  # no network call: a refused link gets no acknowledgment
            except (InvalidLinkError, UnsupportedLinkError) as exc:
                log.outcome = type(exc).__name__
                refusal = (
                    replies.LINK_ERROR
                    if isinstance(exc, InvalidLinkError)
                    else replies.UNSUPPORTED_LINK_ERROR
                )
                await fail(message, refusal, exc)

        if job.attempts == 1:
            if link is None:
                ack = replies.ACK_PASTED_TEXT
            else:
                ack = replies.ACK_SEVERAL_LINKS if route.several_links else replies.ACK_LINK
            await send(message, ack)

        deadline = asyncio.timeout(deadline_s)
        try:
            async with deadline:
                if link is not None:
                    draft = await draft_from_link(http, llm, link)
                else:
                    draft = await draft_from_text(llm, message.text)
            reply = render_reply(draft, link=link)
        except Exception as exc:
            log.outcome = type(exc).__name__
            if isinstance(exc, GenerationFailedError):
                log.model_calls = len(exc.attempts)
            # Only this deadline is handled here: another TimeoutError is an unknown error.
            if isinstance(exc, TimeoutError) and deadline.expired():
                await fail(
                    message,
                    replies.INSIDE_ERROR,
                    exc,
                    f"TimeoutError: deadline of {deadline_s:g} s expired",
                )
            text = _error_text(exc)
            if text is None:
                if job.attempts >= job.max_attempts:
                    await send_inside_text_quietly(message)
                raise  # left to the queue's retries
            await fail(message, text, exc)

        log.source_chars = draft.source_chars
        log.evidence_count = len(draft.evidence.items)
        log.model_calls = len(draft.result.attempts)
        log.removed_quotes = draft.result.removed_quotes
        log.warnings = [w.value for w in warnings_for(draft, pasted=link is None)]
        await send(message, reply)

    async def handle_inbound_message(job: ClaimedJob) -> None:
        started = time.perf_counter()
        log = _JobLog()
        try:
            await run(job, log)
        except BaseException as exc:  # logged, then re-raised: CancelledError included
            if log.outcome == "ok":
                log.outcome = type(exc).__name__
            raise
        finally:
            _log_job(log, llm.model, started)

    return {INBOUND_MESSAGE_KIND: handle_inbound_message}


def _log_job(log: _JobLog, model: str, started: float) -> None:
    logger.log(
        logging.INFO if log.outcome == "ok" else logging.WARNING,
        "inbound message %s -> %s",
        log.route,
        log.outcome,
        extra={
            "route": log.route,
            "outcome": log.outcome,
            "host": log.host,
            "source_chars": log.source_chars,
            "evidence_count": log.evidence_count,
            "model_calls": log.model_calls,
            "removed_quotes": log.removed_quotes,
            "warnings": log.warnings,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
