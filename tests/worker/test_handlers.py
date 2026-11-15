"""The `inbound_message` handler (ADR-0019): routing, acknowledgment, deadline, error families,
reply. No real network (httpx.MockTransport) and no real model (FakeLLM)."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel

from dzdia_newsroom.channels.base import (
    INBOUND_MESSAGE_KIND,
    ChannelError,
    PermanentChannelError,
)
from dzdia_newsroom.channels.telegram import parse_update
from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.guardrails.ssrf import BlockedUrlError
from dzdia_newsroom.llm import FakeLLM, Structured
from dzdia_newsroom.tools.article import (
    PROMPT_VERSION,
    ArticleDraft,
    Paragraph,
    SentenceSegment,
)
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.handlers import build_handlers
from dzdia_newsroom.worker.queue import Handler, PermanentJobError, RetryLaterJobError
from tests.telegram_updates import make_update, team_update


@dataclass
class FakeChannel:
    sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    error: Exception | None = None  # raised on every send
    errors_by_text: dict[str, Exception] = field(default_factory=dict)

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        if self.error is not None:
            raise self.error
        if text in self.errors_by_text:
            raise self.errors_by_text[text]
        self.sent.append((chat_id, text, reply_to))


def inbound_job(
    payload: object = None,
    *,
    text: str = "Bonjour",
    group: bool = False,
    attempts: int = 1,
    max_attempts: int = 3,
) -> ClaimedJob:
    if payload is None:
        update = (
            team_update(user_id=111, text=text) if group else make_update(user_id=111, text=text)
        )
        message = parse_update(update)
        payload = message.model_dump(mode="json")  # type: ignore[union-attr]
    return ClaimedJob.model_validate(
        {
            "id": 1,
            "kind": INBOUND_MESSAGE_KIND,
            "payload": payload,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "claim_token": uuid4(),
        }
    )


TITLE = "Oran : une station de dessalement"
PARAGRAPHS = (
    "<p>Le ministre a inauguré mardi une nouvelle station de dessalement à Oran, d'une capacité "
    "de 300 000 mètres cubes par jour, selon un communiqué publié par le ministère.</p>\n"
    "<p>Le projet a coûté 450 millions de dollars et alimentera trois wilayas de l'ouest du pays, "
    "a précisé le directeur de l'Algerian Energy Company lors de la cérémonie.</p>\n"
    "<p>Les travaux avaient commencé il y a quatre ans, et la mise en service progressive doit "
    "s'achever avant l'été prochain, selon le même communiqué du ministère.</p>"
)
LINK = "https://example.org/secret-path?token=abc"
PASTED = (
    "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre régional, devant un public "
    "nombreux venu de toute la région.\n"
    "Douze films sont en compétition cette année, dont quatre premières oeuvres, selon les "
    "organisateurs qui annoncent aussi des ateliers pour les jeunes réalisateurs.\n"
    "La clôture est prévue le 10 mai avec la remise des prix par le jury international."
)


def page(extra: str = "") -> str:
    return (
        f'<html><head><title>{TITLE}</title><meta property="og:title" content="{TITLE}">'
        f"</head><body><article><h1>{TITLE}</h1>\n{PARAGRAPHS}\n{extra}</article></body></html>"
    )


def html(body: str, status: int = 200, content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, text=body)


def article(evidence_id: str = "E1") -> ArticleDraft:
    return ArticleDraft(
        title="Station de dessalement",
        paragraphs=[
            Paragraph(
                segments=[
                    SentenceSegment(
                        kind="sentence",
                        text="Une station a été inaugurée à Oran.",
                        evidence_ids=[evidence_id],
                    )
                ]
            )
        ],
    )


ARTICLE_TEXT = "Station de dessalement\n\nUne station a été inaugurée à Oran."


class SlowLLM:
    """A generator that never answers in time."""

    model = "slow-llm"
    thinking_level = None

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]:
        self.started.set()
        await asyncio.sleep(30)
        raise AssertionError("the deadline should have expired")


type Respond = Callable[[httpx.Request], httpx.Response]


@dataclass
class Pages:
    """The guarded client of the worker, on a mock transport that records requests."""

    respond: Respond = field(default=lambda request: html(page()))
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


@pytest.fixture
async def pages() -> AsyncIterator[tuple[Pages, httpx.AsyncClient]]:
    recorder = Pages()
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle)) as client:
        yield recorder, client


def handler_for(
    channel: FakeChannel, client: httpx.AsyncClient, llm: Any, **kwargs: float
) -> Handler:
    return build_handlers(channel, client, llm, **kwargs)[INBOUND_MESSAGE_KIND]


# --- routing ---------------------------------------------------------------------------------


async def test_a_short_private_message_gets_the_help(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    llm = FakeLLM([])
    await handler_for(channel, client, llm)(inbound_job(text="Bonjour"))
    assert channel.sent == [("111", replies.HELP_TEXT, "42")]
    assert recorder.requests == [] and llm.prompts == []


@pytest.mark.parametrize("text", ["Bonjour", PASTED, "www.example.org/article"])
async def test_a_group_message_without_link_gets_no_answer(
    pages: tuple[Pages, httpx.AsyncClient], text: str
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    llm = FakeLLM([])
    await handler_for(channel, client, llm)(inbound_job(text=text, group=True))
    assert channel.sent == []
    assert recorder.requests == [] and llm.prompts == []


# --- success ---------------------------------------------------------------------------------


async def test_a_link_is_acknowledged_then_answered_with_a_draft(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=f"Lis {LINK}."))
    assert channel.sent == [
        ("111", replies.ACK_LINK, "42"),
        ("111", f"{ARTICLE_TEXT}\n\nSource : {LINK}", "42"),
    ]
    assert [str(request.url) for request in recorder.requests] == [LINK]


async def test_a_link_in_the_team_group_is_answered_in_the_group(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=LINK, group=True))
    assert [(chat, reply_to) for chat, _, reply_to in channel.sent] == [
        ("-100123", "42"),
        ("-100123", "42"),
    ]


async def test_no_acknowledgment_after_the_first_attempt(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=LINK, attempts=2))
    assert channel.sent == [("111", f"{ARTICLE_TEXT}\n\nSource : {LINK}", "42")]


async def test_several_links_are_acknowledged_as_such_and_only_the_first_is_fetched(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    text = f"{LINK} et https://other.example/b"
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=text))
    assert channel.sent[0] == ("111", replies.ACK_SEVERAL_LINKS, "42")
    assert [str(request.url) for request in recorder.requests] == [LINK]
    assert channel.sent[1][1].endswith(f"Source : {LINK}")


async def test_a_pasted_text_is_acknowledged_then_answered(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    llm = FakeLLM([article()])
    await handler_for(channel, client, llm)(inbound_job(text=PASTED))
    assert channel.sent == [
        ("111", replies.ACK_PASTED_TEXT, "42"),
        ("111", f"{ARTICLE_TEXT}\n\nSource : texte collé", "42"),
    ]
    assert recorder.requests == []
    assert "Douze films sont en compétition" in llm.prompts[0]


# --- warnings --------------------------------------------------------------------------------


async def test_a_paywalled_page_gets_its_warning(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    recorder, client = pages
    recorder.respond = lambda request: html(page("<p>Ce contenu est réservé aux abonnés.</p>"))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=LINK))
    assert channel.sent[1][1].startswith(f"{replies.PAYWALL_WARNING}\n\n{ARTICLE_TEXT}")


async def test_a_long_page_gets_the_truncation_warning(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    long_paragraphs = "\n".join(
        "<p>"
        + f"Le chantier {n} avance selon le calendrier du ministère, bilan numéro {n}. " * 6
        + "</p>"
        for n in range(80)
    )
    recorder.respond = lambda request: html(page(long_paragraphs))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=LINK))
    assert channel.sent[1][1].startswith(f"{replies.TRUNCATED_WARNING}\n\n{ARTICLE_TEXT}")


async def test_a_long_pasted_text_gets_the_telegram_warning(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    text = "\n".join([PASTED] * 12)
    assert len(text) >= 4000
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=text))
    assert channel.sent[1][1].startswith(f"{replies.LONG_PASTE_WARNING}\n\n{ARTICLE_TEXT}")


# --- links refused before the network --------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://www.instagram.com/reel/abc", replies.UNSUPPORTED_LINK_ERROR),
        ("https://youtu.be/xyz", replies.UNSUPPORTED_LINK_ERROR),
        ("https://example.org:8080/a", replies.LINK_ERROR),
        ("https://user:pass@example.org/a", replies.LINK_ERROR),
    ],
)
async def test_a_link_refused_before_the_network_gets_its_text_and_no_acknowledgment(
    pages: tuple[Pages, httpx.AsyncClient], link: str, expected: str
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    with pytest.raises(PermanentJobError) as raised:
        await handler_for(channel, client, FakeLLM([]))(inbound_job(text=link))
    assert channel.sent == [("111", expected, "42")]
    assert recorder.requests == []
    assert "pass" not in str(raised.value) and "/a" not in str(raised.value)


# --- error families --------------------------------------------------------------------------


def _blocked(request: httpx.Request) -> httpx.Response:
    raise BlockedUrlError("host 'example.org' resolves to an address that is not allowed")


ERROR_CASES: dict[str, tuple[Respond, list[Any], str, str]] = {
    "not found": (lambda r: html("", 404), [], replies.LINK_ERROR, "FetchError"),
    "gone": (lambda r: html("", 410), [], replies.LINK_ERROR, "FetchError"),
    "blocked": (_blocked, [], replies.LINK_ERROR, "BlockedUrlError"),
    "refused": (lambda r: html("", 403), [], replies.SITE_ERROR, "FetchError"),
    "rate limited": (lambda r: html("", 429), [], replies.SITE_ERROR, "FetchError"),
    "not html": (
        lambda r: html("%PDF", content_type="application/pdf"),
        [],
        replies.SITE_ERROR,
        "FetchError",
    ),
    "no readable text": (
        lambda r: html("<html><body><p>Trop court.</p></body></html>"),
        [],
        replies.SITE_ERROR,
        "NoReadableTextError",
    ),
    "redirect to a social site": (
        lambda r: httpx.Response(302, headers={"location": "https://www.tiktok.com/@a/video/1"}),
        [],
        replies.UNSUPPORTED_LINK_ERROR,
        "UnsupportedLinkError",
    ),
    "generation failed": (
        lambda r: html(page()),
        [article("E99"), article("E99")],
        replies.INSIDE_ERROR,
        "GenerationFailedError",
    ),
}


@pytest.mark.parametrize("case", list(ERROR_CASES))
async def test_each_error_family_sends_its_text_then_is_permanent(
    pages: tuple[Pages, httpx.AsyncClient], case: str
) -> None:
    respond, llm_replies, expected, error_type = ERROR_CASES[case]
    recorder, client = pages
    recorder.respond = respond
    channel = FakeChannel()
    with pytest.raises(PermanentJobError) as raised:
        await handler_for(channel, client, FakeLLM(llm_replies))(inbound_job(text=LINK))
    assert channel.sent == [("111", replies.ACK_LINK, "42"), ("111", expected, "42")]
    message = str(raised.value)
    assert message.startswith(f"{error_type}: ")
    assert "secret-path" not in message and "token" not in message


async def test_an_error_family_is_permanent_on_a_later_attempt_too(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    recorder.respond = lambda r: html("", 404)
    channel = FakeChannel()
    with pytest.raises(PermanentJobError):
        await handler_for(channel, client, FakeLLM([]))(inbound_job(text=LINK, attempts=3))
    assert channel.sent == [("111", replies.LINK_ERROR, "42")]


async def test_a_short_pasted_text_never_reaches_the_pipeline(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([]))(inbound_job(text="a" * 299))
    assert channel.sent == [("111", replies.HELP_TEXT, "42")]


# --- deadline --------------------------------------------------------------------------------


async def test_the_deadline_sends_the_inside_text_and_is_permanent(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, SlowLLM(), deadline_s=0.05)
    with pytest.raises(PermanentJobError, match="TimeoutError"):
        await handler(inbound_job(text=LINK))
    assert channel.sent == [("111", replies.ACK_LINK, "42"), ("111", replies.INSIDE_ERROR, "42")]


async def test_another_timeout_is_not_the_deadline(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([TimeoutError("read timeout")]))
    with pytest.raises(TimeoutError, match="read timeout"):
        await handler(inbound_job(text=LINK))
    assert channel.sent == [("111", replies.ACK_LINK, "42")]


async def test_the_deadline_does_not_cover_the_sends(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages

    @dataclass
    class SlowChannel(FakeChannel):
        async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
            await asyncio.sleep(0.6)  # each send alone outlasts the deadline
            await super().send_text(chat_id, text, reply_to=reply_to)

    channel = SlowChannel()
    handler = handler_for(channel, client, FakeLLM([article()]), deadline_s=0.5)
    await handler(inbound_job(text=LINK))
    assert [text for _, text, _ in channel.sent] == [
        replies.ACK_LINK,
        f"{ARTICLE_TEXT}\n\nSource : {LINK}",
    ]


async def test_a_cancelled_job_is_not_caught(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    _, client = pages
    channel = FakeChannel()
    llm = SlowLLM()
    task = asyncio.create_task(
        handler_for(channel, client, llm)(inbound_job(text=LINK, attempts=3))
    )
    await llm.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert channel.sent == []


# --- other errors ----------------------------------------------------------------------------


async def test_an_unknown_error_is_left_to_the_queue_with_no_message(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(RuntimeError, match="provider down"):
        await handler(inbound_job(text=LINK, attempts=2, max_attempts=3))
    assert channel.sent == []


async def test_an_unknown_error_on_the_last_attempt_sends_the_inside_text_first(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(RuntimeError, match="provider down"):
        await handler(inbound_job(text=LINK, attempts=3, max_attempts=3))
    assert channel.sent == [("111", replies.INSIDE_ERROR, "42")]


async def test_a_failed_inside_text_does_not_replace_the_original_error(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    channel = FakeChannel(
        errors_by_text={replies.INSIDE_ERROR: PermanentChannelError("sendMessage: rejected")}
    )
    handler = handler_for(channel, client, FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(RuntimeError, match="provider down"):
        await handler(inbound_job(text=LINK, attempts=3, max_attempts=3))
    assert any("inside" in record.getMessage() for record in caplog.records)


# --- channel errors --------------------------------------------------------------------------


async def test_rejected_send_is_permanent(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    _, client = pages
    channel = FakeChannel(error=PermanentChannelError("sendMessage: rejected (403)"))
    with pytest.raises(PermanentJobError, match="rejected"):
        await handler_for(channel, client, FakeLLM([]))(inbound_job())


async def test_transient_send_failure_is_left_to_the_queue_retries(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel(error=ChannelError("sendMessage: server error 502"))
    with pytest.raises(ChannelError) as raised:
        await handler_for(channel, client, FakeLLM([]))(inbound_job())
    assert not isinstance(raised.value, PermanentJobError)


async def test_rate_limit_is_passed_to_the_queue_as_a_minimum_delay(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel(error=ChannelError("sendMessage: rate limited", retry_after_s=120))
    with pytest.raises(RetryLaterJobError) as raised:
        await handler_for(channel, client, FakeLLM([]))(inbound_job())
    assert raised.value.delay_s == 120


async def test_a_failed_acknowledgment_is_translated_too(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel(errors_by_text={replies.ACK_LINK: ChannelError("x", retry_after_s=5)})
    with pytest.raises(RetryLaterJobError):
        await handler_for(channel, client, FakeLLM([]))(inbound_job(text=LINK))
    assert recorder.requests == []


async def test_a_failed_error_text_is_translated(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel(
        errors_by_text={replies.UNSUPPORTED_LINK_ERROR: PermanentChannelError("rejected (403)")}
    )
    with pytest.raises(PermanentJobError, match="rejected"):
        await handler_for(channel, client, FakeLLM([]))(inbound_job(text="https://x.com/a/1"))


async def test_invalid_payload_is_permanent_and_does_not_leak_values(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    handler = handler_for(FakeChannel(), client, FakeLLM([]))
    with pytest.raises(PermanentJobError) as raised:
        await handler(inbound_job({"text": "texte confidentiel"}))
    assert "confidentiel" not in str(raised.value)


# --- log line (ADR-0019, point 11) -----------------------------------------------------------


def _job_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "dzdia_newsroom.worker.handlers"]


async def test_one_log_line_per_job_without_text_path_or_article(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    recorder, client = pages
    recorder.respond = lambda request: html(page("<p>Ce contenu est réservé aux abonnés.</p>"))
    caplog.set_level(logging.INFO)
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=f"Vu {LINK}"))

    [record] = _job_records(caplog)
    fields = record.__dict__
    assert fields["route"] == "link"
    assert fields["outcome"] == "ok"
    assert fields["host"] == "example.org"
    assert fields["source_chars"] > 300
    assert fields["evidence_count"] >= 3
    assert fields["model_calls"] == 1
    assert fields["removed_quotes"] == 0
    assert fields["warnings"] == ["paywall"]
    assert fields["model"] == "fake-llm"
    assert fields["prompt_version"] == PROMPT_VERSION
    assert isinstance(fields["duration_ms"], int)
    logged = f"{record.getMessage()} {fields}"
    assert "secret-path" not in logged and "token=" not in logged
    assert "Vu " not in logged and "inaugurée" not in logged


async def test_the_log_line_names_the_error_type(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    with pytest.raises(PermanentJobError):
        await handler_for(FakeChannel(), client, FakeLLM([article("E9"), article("E9")]))(
            inbound_job(text=LINK)
        )
    [record] = _job_records(caplog)
    assert record.__dict__["outcome"] == "GenerationFailedError"
    assert record.__dict__["model_calls"] == 2


@pytest.mark.parametrize(
    ("text", "group", "route"),
    [("Bonjour", False, "help"), ("Bonjour", True, "silent"), (PASTED, False, "pasted_text")],
)
async def test_the_log_line_names_the_route(
    pages: tuple[Pages, httpx.AsyncClient],
    caplog: pytest.LogCaptureFixture,
    text: str,
    group: bool,
    route: str,
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    await handler_for(FakeChannel(), client, FakeLLM([article()]))(
        inbound_job(text=text, group=group)
    )
    [record] = _job_records(caplog)
    assert record.__dict__["route"] == route
    assert record.__dict__["outcome"] == "ok"
    assert record.__dict__["host"] is None
