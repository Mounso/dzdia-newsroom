"""C-2: network errors and timeouts are retried, every attempt is logged with latency, tokens and
cost, and an unusable structured output raises a typed error that keeps the raw text."""

import logging
from typing import Any

import httpx
import pytest
from google.genai import errors, types
from pydantic import BaseModel

from dzdia_newsroom.llm import GeminiClient, StructuredOutputError, Usage
from dzdia_newsroom.llm.gemini import is_retryable_gemini_error

MODEL = "gemini-3.1-flash-lite-preview"


class _Out(BaseModel):
    ok: bool


def _response(text: str, finish_reason: str = "STOP") -> types.GenerateContentResponse:
    return types.GenerateContentResponse.model_validate(
        {
            "candidates": [
                {"content": {"parts": [{"text": text}]}, "finish_reason": finish_reason}
            ],
            "usage_metadata": {
                "prompt_token_count": 1000,
                "candidates_token_count": 400,
                "thoughts_token_count": 0,
            },
        }
    )


def _client(
    monkeypatch: pytest.MonkeyPatch, replies: list[Any]
) -> tuple[GeminiClient, list[float]]:
    waits: list[float] = []

    async def no_sleep(delay: float) -> None:
        waits.append(delay)

    client = GeminiClient("test-key", MODEL, max_rpm=60_000, retry_sleep=no_sleep)

    async def scripted(*, model: str, contents: str, config: Any) -> Any:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(client._client.aio.models, "generate_content", scripted)
    return client, waits


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ReadTimeout("read timed out"), True),
        (httpx.ConnectError("connection refused"), True),
        (httpx.RemoteProtocolError("server disconnected"), True),
        (errors.ServerError(503, {"error": "unavailable"}), True),
        (errors.ClientError(429, {"error": "rate limited"}), True),
        (errors.ClientError(400, {"error": "bad request"}), False),
        (ValueError("bug"), False),
    ],
)
def test_retryable_errors(exc: Exception, expected: bool) -> None:
    assert is_retryable_gemini_error(exc) is expected


async def test_a_timeout_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client, waits = _client(monkeypatch, [httpx.ReadTimeout("slow"), _response('{"ok": true}')])
    result = await client.generate_structured("prompt", _Out)
    assert result.value == _Out(ok=True)
    assert len(waits) == 1


async def test_a_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client, waits = _client(monkeypatch, [errors.ClientError(400, {"error": "bad"})])
    with pytest.raises(errors.ClientError):
        await client.generate("prompt")
    assert waits == []


async def test_each_attempt_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="dzdia_newsroom.llm.gemini")
    client, _ = _client(monkeypatch, [httpx.ConnectError("down"), _response('{"ok": true}')])
    await client.generate_structured("prompt", _Out)

    failed, ok = caplog.records
    assert failed.levelno == logging.WARNING
    assert (failed.attempt, failed.retryable, failed.model) == (1, True, MODEL)  # type: ignore[attr-defined]
    assert ok.levelno == logging.INFO
    assert (ok.attempt, ok.input_tokens, ok.output_tokens) == (2, 1000, 400)  # type: ignore[attr-defined]
    # 1000 x 0.25 + 400 x 1.50 per million tokens.
    assert ok.cost_usd == pytest.approx(0.00085)  # type: ignore[attr-defined]
    assert ok.finish_reason == "STOP"  # type: ignore[attr-defined]
    assert isinstance(ok.latency_ms, int)  # type: ignore[attr-defined]


async def test_invalid_json_raises_a_typed_error_with_the_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, waits = _client(monkeypatch, [_response('{"ok": tr', finish_reason="MAX_TOKENS")])
    with pytest.raises(StructuredOutputError) as info:
        await client.generate_structured("prompt", _Out)
    assert info.value.raw_text == '{"ok": tr'
    assert info.value.finish_reason == "MAX_TOKENS"
    assert info.value.usage == Usage(1000, 400, 0)
    assert waits == []  # the call succeeded and was billed: asking again is the caller's choice


async def test_an_empty_response_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, [_response("", finish_reason="RECITATION")])
    with pytest.raises(StructuredOutputError, match="empty response") as info:
        await client.generate_structured("prompt", _Out)
    assert info.value.finish_reason == "RECITATION"
