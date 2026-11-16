"""Job handlers, keyed by job kind.

`inbound_message` runs one message in one job, as a thin adapter over `worker/replies.py` (what
to say) and the tools (what to do):
- team group (ADR-0019): a link gets an acknowledgment on the first attempt, then a draft
  composed under a deadline (`tools/draft.py`);
- private chat (ADR-0020): links and pasted texts join the chat's dossier (`tools/dossier.py`)
  and get one receipt; `/ecris` writes the article from the whole dossier and sends it with its
  sources; `/nouveau` empties the dossier; anything else gets the help.

Errors (ADR-0020, point 14, amending ADR-0019, point 6 and ADR-0018, point 13):
- an answered refusal is a finished job: a typed error of the link or the site family, a full
  dossier or a link already there, and an empty dossier send their message and the job ends
  `done`, its log line at WARNING with the error type as outcome;
- `GenerationFailedError` and the expired deadline send their message, then raise
  `PermanentJobError`: the job is `dead`;
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
from pydantic import JsonValue, ValidationError

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
from dzdia_newsroom.tools.dossier import (
    Added,
    DossierKey,
    DossierState,
    DossierStore,
    EmptyDossierError,
    Refused,
    WrittenArticle,
    add_link,
    add_text,
    clear,
    read_state,
    write_article,
)
from dzdia_newsroom.tools.dossier_article import DOSSIER_PROMPT_VERSION, render_dossier_text
from dzdia_newsroom.tools.draft import draft_from_link
from dzdia_newsroom.tools.fetch_page import (
    FetchError,
    InvalidLinkError,
    UnsupportedLinkError,
    check_link,
)
from dzdia_newsroom.tools.page_text import NoReadableTextError
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.queue import Handler, PermanentJobError, RetryLaterJobError
from dzdia_newsroom.worker.replies import Route, RouteKind, host_of, route_message

logger = logging.getLogger(__name__)

# Covers the fetches and the generation of one job, never a send (ADR-0019, point 5; ADR-0020,
# point 4).
DEFAULT_DEADLINE_S = 180.0
_MAX_ERROR_CHARS = 300
_OK = "ok"
# The typed errors of one link of a private message: each becomes that link's receipt line.
_LINK_ERRORS = (
    InvalidLinkError,
    UnsupportedLinkError,
    BlockedUrlError,
    FetchError,
    NoReadableTextError,
)


@dataclass
class _JobLog:
    """The fields of the single log line of a job (ADR-0019, point 11; ADR-0020, point 15).
    Never a message text, the path or query of a link, or the article."""

    route: str | None = None
    outcome: str = _OK
    host: str | None = None
    source_chars: int | None = None
    evidence_count: int | None = None
    model_calls: int = 0
    removed_quotes: int | None = None
    warnings: list[str] = field(default_factory=list)
    prompt_version: str | None = None
    dossier_sources: int | None = None
    dossier_chars: int | None = None
    links: list[dict[str, JsonValue]] = field(default_factory=list)
    budget_chars: int | None = None
    max_words: int | None = None
    article_chars: int | None = None
    paragraphs: int | None = None
    headings: int | None = None
    sources_cited: list[int] | None = None
    sources_unused: list[int] | None = None

    def refuse(self, outcome: str) -> None:
        """Keep the first refusal of a source job as its outcome."""
        if self.outcome == _OK:
            self.outcome = outcome

    def dossier(self, state: DossierState) -> None:
        self.dossier_sources = state.count
        self.dossier_chars = state.total_chars

    def article(self, written: WrittenArticle) -> None:
        article = written.result.article
        cited = sorted({source for sources in written.paragraph_sources for source in sources})
        self.dossier_sources = len(written.sources)
        self.dossier_chars = sum(len(source.text) for source in written.sources)
        self.evidence_count = len(written.evidence.items)
        self.model_calls = len(written.result.attempts)
        self.removed_quotes = written.result.removed_quotes
        self.budget_chars = written.budget.chars
        self.max_words = written.budget.max_words
        self.article_chars = len(render_dossier_text(article, written.evidence))
        self.paragraphs = len(article.paragraphs)
        self.headings = sum(1 for paragraph in article.paragraphs if paragraph.heading.strip())
        self.sources_cited = cited
        self.sources_unused = sorted(set(range(1, len(written.sources) + 1)) - set(cited))


def _describe(exc: BaseException) -> str:
    """The error type and its own short message: never the message text or the full link."""
    return f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]


def build_handlers(
    channel: Channel,
    http: httpx.AsyncClient,
    llm: StructuredGenerator,
    dossiers: DossierStore,
    *,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> dict[str, Handler]:
    """`http` must be the guarded client (`guarded_client`): it fetches the journalists' links.
    `dossiers` holds the private chats' dossiers (`db.dossiers.DossierRepository`)."""

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

    async def send_quietly(message: InboundMessage, text: str) -> None:
        """The last attempt's message: a failure to send it must not hide the original error."""
        try:
            await channel.send_text(message.chat_id, text, reply_to=message.message_id)
        except ChannelError as exc:
            logger.warning(
                "could not send the inside text on the last attempt (%s)", _describe(exc)
            )

    def deadline_description() -> str:
        return f"TimeoutError: deadline of {deadline_s:g} s expired"

    async def run_link(
        job: ClaimedJob, message: InboundMessage, route: Route, log: _JobLog
    ) -> None:
        """Team group: one draft from the first link (ADR-0019)."""
        log.prompt_version = PROMPT_VERSION
        link = route.link
        if link is None:  # never: a LINK route always carries its link
            raise PermanentJobError("a link route without a link")
        log.host = host_of(link)
        try:
            check_link(link)  # no network call: a refused link gets no acknowledgment
        except (InvalidLinkError, UnsupportedLinkError) as exc:
            log.outcome = type(exc).__name__
            await send(message, replies.group_error_text(exc) or replies.LINK_ERROR)
            return

        if job.attempts == 1:
            await send(
                message, replies.ACK_SEVERAL_LINKS if route.several_links else replies.ACK_LINK
            )

        deadline = asyncio.timeout(deadline_s)
        try:
            async with deadline:
                draft = await draft_from_link(http, llm, link)
            reply = replies.render_reply(draft, link=link)
        except Exception as exc:
            log.outcome = type(exc).__name__
            # Only this deadline is handled here: another TimeoutError is an unknown error.
            if isinstance(exc, TimeoutError) and deadline.expired():
                await fail(message, replies.INSIDE_ERROR, exc, deadline_description())
            if isinstance(exc, GenerationFailedError):
                log.model_calls = len(exc.attempts)
                await fail(message, replies.INSIDE_ERROR, exc)
            text = replies.group_error_text(exc)
            if text is None:
                if job.attempts >= job.max_attempts:
                    await send_quietly(message, replies.INSIDE_ERROR)
                raise  # left to the queue's retries
            await send(message, text)  # an answered refusal: the job is done
            return

        log.source_chars = draft.source_chars
        log.evidence_count = len(draft.evidence.items)
        log.model_calls = len(draft.result.attempts)
        log.removed_quotes = draft.result.removed_quotes
        log.warnings = [warning.value for warning in replies.warnings_for(draft)]
        await send(message, reply)

    async def run_source(
        job: ClaimedJob, message: InboundMessage, route: Route, key: DossierKey, log: _JobLog
    ) -> None:
        """Private chat: every link of the message, or its pasted text, joins the dossier; one
        receipt (ADR-0020, point 4). No acknowledgment before the fetches."""
        lines: list[str] = []
        deadline = asyncio.timeout(deadline_s)
        try:
            async with deadline:
                for index, link in enumerate(route.links):
                    host = host_of(link)
                    try:
                        outcome = await add_link(
                            dossiers,
                            http,
                            key,
                            link,
                            message_id=message.message_id,
                            link_index=index,
                        )
                    except _LINK_ERRORS as exc:
                        lines.append(replies.link_error_line(exc, host))
                        log.links.append({"host": host, "outcome": type(exc).__name__})
                        log.refuse(type(exc).__name__)
                        continue
                    lines.append(_receipt_line(outcome, host=host, pasted=False))
                    result = _OK if isinstance(outcome, Added) else outcome.reason.value
                    log.links.append({"host": host, "outcome": result})
                    if isinstance(outcome, Refused):
                        log.refuse(outcome.reason.value)
                if not route.links:
                    outcome = await add_text(
                        dossiers, key, message.text, message_id=message.message_id
                    )
                    lines.append(_receipt_line(outcome, host=None, pasted=True))
                    if isinstance(outcome, Refused):
                        log.refuse(outcome.reason.value)
        except Exception as exc:
            log.outcome = type(exc).__name__
            if isinstance(exc, TimeoutError) and deadline.expired():
                await fail(message, replies.SOURCE_INSIDE_ERROR, exc, deadline_description())
            if job.attempts >= job.max_attempts:
                await send_quietly(message, replies.SOURCE_INSIDE_ERROR)
            raise  # left to the queue's retries

        state = await read_state(dossiers, key)
        log.dossier(state)
        await send(message, replies.receipt(lines, state))

    async def run_write(
        job: ClaimedJob, message: InboundMessage, key: DossierKey, log: _JobLog
    ) -> None:
        """Private chat: the article from the whole dossier, then its sources (ADR-0020, point
        12)."""
        log.prompt_version = DOSSIER_PROMPT_VERSION
        state = await read_state(dossiers, key)
        log.dossier(state)
        if state.count == 0:
            log.outcome = EmptyDossierError.__name__
            await send(message, replies.EMPTY_DOSSIER)  # no model call
            return
        if job.attempts == 1:
            await send(message, replies.write_ack(state.count))

        deadline = asyncio.timeout(deadline_s)
        try:
            async with deadline:
                written = await write_article(dossiers, llm, key)
            article_text = replies.article_message(written)
            sources_text = replies.sources_message(written)
        except Exception as exc:
            log.outcome = type(exc).__name__
            if isinstance(exc, TimeoutError) and deadline.expired():
                await fail(message, replies.WRITE_INSIDE_ERROR, exc, deadline_description())
            if isinstance(exc, EmptyDossierError):  # emptied since the first look
                log.dossier(DossierState())
                await send(message, replies.EMPTY_DOSSIER)
                return
            if isinstance(exc, GenerationFailedError):
                log.model_calls = len(exc.attempts)
                await fail(message, replies.WRITE_INSIDE_ERROR, exc)
            if job.attempts >= job.max_attempts:
                await send_quietly(message, replies.WRITE_INSIDE_ERROR)
            raise  # left to the queue's retries

        log.article(written)
        await send(message, article_text)
        await send(message, sources_text)

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
        key = DossierKey(channel=message.channel, chat_id=message.chat_id)
        match route.kind:
            case RouteKind.SILENT:
                return
            case RouteKind.LINK:
                await run_link(job, message, route, log)
            case RouteKind.SOURCE:
                await run_source(job, message, route, key, log)
            case RouteKind.WRITE:
                await run_write(job, message, key, log)
            case RouteKind.NEW:
                removed = await clear(dossiers, key)
                log.dossier(DossierState())
                await send(message, replies.cleared_text(removed))
            case RouteKind.HELP:
                state = await read_state(dossiers, key)
                log.dossier(state)
                await send(message, replies.help_text(state))

    async def handle_inbound_message(job: ClaimedJob) -> None:
        started = time.perf_counter()
        log = _JobLog()
        try:
            await run(job, log)
        except BaseException as exc:  # logged, then re-raised: CancelledError included
            if log.outcome == _OK:
                log.outcome = type(exc).__name__
            raise
        finally:
            _log_job(log, llm.model, started)

    return {INBOUND_MESSAGE_KIND: handle_inbound_message}


def _receipt_line(outcome: Added | Refused, *, host: str | None, pasted: bool) -> str:
    if isinstance(outcome, Added):
        return replies.added_line(outcome)
    return replies.refused_line(outcome, host=host, pasted=pasted)


def _log_job(log: _JobLog, model: str, started: float) -> None:
    logger.log(
        logging.INFO if log.outcome == _OK else logging.WARNING,
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
            "dossier_sources": log.dossier_sources,
            "dossier_chars": log.dossier_chars,
            "links": log.links,
            "budget_chars": log.budget_chars,
            "max_words": log.max_words,
            "article_chars": log.article_chars,
            "paragraphs": log.paragraphs,
            "headings": log.headings,
            "sources_cited": log.sources_cited,
            "sources_unused": log.sources_unused,
            "model": model,
            "prompt_version": log.prompt_version,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
