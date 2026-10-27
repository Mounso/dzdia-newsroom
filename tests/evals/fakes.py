"""Test doubles for the LLM protocols."""

from collections.abc import Callable

from pydantic import BaseModel

from evals.baseline_v1.llm import Generation


class FakeTextGenerator:
    def __init__(self, respond: Callable[[str], str], model: str = "fake-model") -> None:
        self.model = model
        self.prompts: list[str] = []
        self._respond = respond

    async def generate(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        return Generation(
            text=self._respond(prompt), latency_ms=1, input_tokens=10, output_tokens=5
        )


class FakeStructuredGenerator:
    def __init__(self, payload: str, model: str = "fake-extractor") -> None:
        self.model = model
        self.calls = 0
        self._payload = payload

    async def generate_structured[M: BaseModel](self, prompt: str, schema: type[M]) -> M:
        self.calls += 1
        return schema.model_validate_json(self._payload)
