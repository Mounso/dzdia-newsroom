"""Scripted LLM for tests: no network, deterministic, records every prompt."""

from collections import deque
from collections.abc import Iterable

from pydantic import BaseModel, ValidationError

from dzdia_newsroom.llm.base import Generation, Structured, StructuredOutputError, Usage

type Reply = BaseModel | str | Exception

FAKE_USAGE = Usage(input_tokens=10, output_tokens=5, thinking_tokens=0)


class FakeLLM:
    """Implements TextGenerator and StructuredGenerator. Each call takes the next reply:

    - a pydantic model: returned as the structured value (validated against the schema asked);
    - a string: the raw model output, parsed like `GeminiClient` does, so invalid JSON raises
      `StructuredOutputError`;
    - an exception: raised, as a failed API call would.

    Running out of replies is a test bug and raises `AssertionError`.
    """

    def __init__(
        self,
        replies: Iterable[Reply],
        *,
        model: str = "fake-llm",
        thinking_level: str | None = None,
    ) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self.prompts: list[str] = []
        self._replies: deque[Reply] = deque(replies)

    def _next(self, prompt: str) -> BaseModel | str:
        self.prompts.append(prompt)
        if not self._replies:
            raise AssertionError(f"FakeLLM: no reply left for call {len(self.prompts)}")
        reply = self._replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def generate(self, prompt: str) -> Generation:
        reply = self._next(prompt)
        text = reply if isinstance(reply, str) else reply.model_dump_json()
        return Generation(text=text, latency_ms=1, input_tokens=10, output_tokens=5)

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]:
        reply = self._next(prompt)
        raw = reply if isinstance(reply, str) else reply.model_dump_json()
        try:
            value = schema.model_validate_json(raw)
        except ValidationError as exc:
            raise StructuredOutputError(
                "output does not match the schema",
                raw_text=raw,
                finish_reason="STOP",
                usage=FAKE_USAGE,
                latency_ms=1,
            ) from exc
        return Structured(value, FAKE_USAGE, latency_ms=1)
