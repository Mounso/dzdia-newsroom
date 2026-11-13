"""The thinking level reaches structured (extractor) calls only, never the model under test."""

from typing import Any

import pytest
from google.genai import types
from pydantic import BaseModel

from evals.baseline_v1.llm import GeminiClient, Usage


class _Out(BaseModel):
    ok: bool


def _client(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> tuple[GeminiClient, list[Any]]:
    client = GeminiClient("test-key", "test-model", **kwargs)
    configs: list[Any] = []

    async def fake_generate_content(*, model: str, contents: str, config: Any) -> Any:
        configs.append(config)
        return types.GenerateContentResponse.model_validate(
            {
                "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
                "usage_metadata": {
                    "prompt_token_count": 300,
                    "candidates_token_count": 200,
                    "thoughts_token_count": 700,
                },
            }
        )

    monkeypatch.setattr(client._client.aio.models, "generate_content", fake_generate_content)
    return client, configs


async def test_structured_call_sends_the_thinking_level(monkeypatch: pytest.MonkeyPatch) -> None:
    client, configs = _client(monkeypatch, thinking_level="low")
    result = await client.generate_structured("prompt", _Out)
    assert result.value == _Out(ok=True)
    assert result.usage == Usage(input_tokens=300, output_tokens=200, thinking_tokens=700)
    low = types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)
    assert configs[0].thinking_config == low
    assert configs[0].temperature == 0.0


async def test_no_thinking_level_keeps_the_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, configs = _client(monkeypatch)
    await client.generate_structured("prompt", _Out)
    assert configs[0].thinking_config is None


async def test_text_call_keeps_v1_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client, configs = _client(monkeypatch, thinking_level="low", max_rpm=60_000)
    await client.generate("prompt")
    assert configs == [None]
