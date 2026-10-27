import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from dzdia_newsroom.channels.base import ChannelError, PermanentChannelError
from dzdia_newsroom.channels.telegram import TelegramClient

TOKEN = "123456:SECRET-TOKEN"

Responder = Callable[[httpx.Request], httpx.Response]


def ok(result: object = True) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def failure(status: int, description: str = "", **parameters: int) -> httpx.Response:
    body: dict[str, Any] = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return httpx.Response(status, json=body)


class Recorder:
    """Plays scripted responses (or raises scripted errors) and records requests and sleeps."""

    def __init__(self, *outcomes: httpx.Response | Exception) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    def body(self, index: int = 0) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[index].content)
        return parsed


@pytest.fixture
async def http() -> AsyncIterator[Callable[[Recorder], TelegramClient]]:
    clients: list[httpx.AsyncClient] = []

    def build(recorder: Recorder) -> TelegramClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle))
        clients.append(client)
        return TelegramClient(TOKEN, client, sleep=recorder.sleep, rand=lambda: 1.0)

    yield build
    for client in clients:
        await client.aclose()


async def test_send_text_posts_plain_text_as_a_reply(
    http: Callable[[Recorder], TelegramClient],
) -> None:
    recorder = Recorder(ok())
    await http(recorder).send_text("111", "*pas du markdown*", reply_to="42")

    request = recorder.requests[0]
    assert request.url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = recorder.body()
    assert body["chat_id"] == "111"
    assert body["text"] == "*pas du markdown*"
    assert "parse_mode" not in body  # v1 bug #7
    assert body["reply_parameters"] == {"message_id": 42, "allow_sending_without_reply": True}


async def test_set_webhook_sends_secret_and_allowed_updates(
    http: Callable[[Recorder], TelegramClient],
) -> None:
    recorder = Recorder(ok())
    await http(recorder).set_webhook("https://bot.example/webhooks/telegram", "s" * 32)
    assert recorder.body() == {
        "url": "https://bot.example/webhooks/telegram",
        "secret_token": "s" * 32,
        "allowed_updates": ["message"],
        "drop_pending_updates": False,
    }


async def test_server_errors_are_retried_with_backoff(
    http: Callable[[Recorder], TelegramClient],
) -> None:
    recorder = Recorder(failure(502), failure(500), ok())
    await http(recorder).send_text("111", "x")
    assert len(recorder.requests) == 3
    assert recorder.sleeps == [1.0, 2.0]  # base 1 s doubling, rand=1 -> full step


async def test_short_retry_after_is_honored(http: Callable[[Recorder], TelegramClient]) -> None:
    recorder = Recorder(failure(429, "Too Many Requests", retry_after=3), ok())
    await http(recorder).send_text("111", "x")
    assert recorder.sleeps == [3.0]


async def test_long_retry_after_is_left_to_the_job_queue(
    http: Callable[[Recorder], TelegramClient],
) -> None:
    recorder = Recorder(failure(429, "Too Many Requests", retry_after=120))
    with pytest.raises(ChannelError, match="rate limited for 120 s") as raised:
        await http(recorder).send_text("111", "x")
    assert not isinstance(raised.value, PermanentChannelError)
    assert recorder.sleeps == []


@pytest.mark.parametrize("status", [400, 403])
async def test_rejected_requests_are_permanent_and_not_retried(
    http: Callable[[Recorder], TelegramClient], status: int
) -> None:
    recorder = Recorder(failure(status, "Forbidden: bot was blocked by the user"))
    with pytest.raises(PermanentChannelError, match="bot was blocked"):
        await http(recorder).send_text("111", "x")
    assert len(recorder.requests) == 1


async def test_network_errors_are_retried_then_raised_without_the_token(
    http: Callable[[Recorder], TelegramClient],
) -> None:
    error = httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/sendMessage")
    recorder = Recorder(error, error, error)
    with pytest.raises(ChannelError) as raised:
        await http(recorder).send_text("111", "x")
    assert len(recorder.requests) == 3
    assert TOKEN not in str(raised.value)
    assert "<token>" in str(raised.value)


async def test_non_json_error_body_is_still_classified(
    http: Callable[[Recorder], TelegramClient],
) -> None:
    recorder = Recorder(httpx.Response(502, text="<html>Bad Gateway</html>"), ok())
    await http(recorder).send_text("111", "x")
    assert len(recorder.requests) == 2
