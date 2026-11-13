import pytest
from pydantic import BaseModel

from dzdia_newsroom.llm import FakeLLM, StructuredOutputError, Usage
from dzdia_newsroom.llm.pricing import estimate_cost_usd


class _Out(BaseModel):
    ok: bool


async def test_fake_llm_replays_its_script_in_order() -> None:
    llm = FakeLLM([_Out(ok=True), '{"ok": false}', TimeoutError("boom")])
    assert (await llm.generate_structured("p1", _Out)).value == _Out(ok=True)
    assert (await llm.generate_structured("p2", _Out)).value == _Out(ok=False)
    with pytest.raises(TimeoutError):
        await llm.generate_structured("p3", _Out)
    assert llm.prompts == ["p1", "p2", "p3"]


async def test_fake_llm_parses_raw_text_like_the_real_client() -> None:
    llm = FakeLLM(["not json"])
    with pytest.raises(StructuredOutputError) as info:
        await llm.generate_structured("p", _Out)
    assert info.value.raw_text == "not json"


async def test_fake_llm_fails_loudly_when_the_script_runs_out() -> None:
    llm = FakeLLM([])
    with pytest.raises(AssertionError, match="no reply left"):
        await llm.generate("p")


def test_cost_counts_thinking_tokens_as_output() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000, thinking_tokens=500_000)
    assert estimate_cost_usd("gemini-3.1-pro-preview", usage) == pytest.approx(2.0 + 12.0)


def test_cost_is_unknown_for_an_unpriced_model_or_missing_counts() -> None:
    assert estimate_cost_usd("some-other-model", Usage(10, 10)) is None
    assert estimate_cost_usd("gemini-3.1-pro-preview", Usage()) is None
