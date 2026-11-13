"""Provider-neutral types of the LLM layer: results, usage, protocols and errors."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Generation:
    text: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None  # billed as output


@dataclass(frozen=True)
class Structured[M: BaseModel]:
    value: M
    usage: Usage
    latency_ms: int | None = None  # the successful call only: no queueing, no failed attempts


class StructuredOutputError(Exception):
    """The model answered, but its output is not a valid instance of the schema: invalid or
    truncated JSON, a schema mismatch, or an empty response (e.g. a SAFETY or RECITATION block).

    Not retried by the client: the call itself succeeded and was billed. The caller decides
    whether to ask again (the article's validation-and-retry loop does, ADR-0009 point 4).
    """

    def __init__(
        self,
        message: str,
        *,
        raw_text: str,
        finish_reason: str | None,
        usage: Usage,
        latency_ms: int | None,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.finish_reason = finish_reason
        self.usage = usage
        self.latency_ms = latency_ms


class TextGenerator(Protocol):
    model: str

    async def generate(self, prompt: str, *, temperature: float | None = None) -> Generation:
        """Free text. `temperature` None keeps the model's defaults (what v1 sent)."""
        ...


class StructuredGenerator(Protocol):
    model: str

    @property
    def thinking_level(self) -> str | None: ...

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]: ...
