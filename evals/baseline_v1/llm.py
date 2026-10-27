"""Thin async Gemini client for the baseline: bounded concurrency, timeouts, retries on 429/5xx."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Generation:
    text: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


class TextGenerator(Protocol):
    model: str

    async def generate(self, prompt: str) -> Generation: ...


class StructuredGenerator(Protocol):
    model: str

    async def generate_structured[M: BaseModel](self, prompt: str, schema: type[M]) -> M: ...


async def retry_async[T](
    call: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool],
    max_retries: int = 4,
    base_delay_s: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `call`, retrying retryable errors with exponential backoff and jitter."""
    attempt = 0
    while True:
        try:
            return await call()
        except Exception as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise
            await sleep(min(60.0, base_delay_s * 2**attempt) + random.uniform(0, 1))
            attempt += 1


def is_retryable_gemini_error(exc: Exception) -> bool:
    return isinstance(exc, errors.APIError) and exc.code in RETRYABLE_STATUS


class GeminiClient:
    """Implements TextGenerator and StructuredGenerator on top of google-genai."""

    def __init__(
        self, api_key: str, model: str, *, max_concurrency: int = 2, timeout_s: float = 90.0
    ) -> None:
        self.model = model
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def _call(
        self, prompt: str, config: types.GenerateContentConfig | None
    ) -> tuple[types.GenerateContentResponse, int]:
        async def once() -> tuple[types.GenerateContentResponse, int]:
            async with self._semaphore:
                start = time.perf_counter()
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                return response, int((time.perf_counter() - start) * 1000)

        return await retry_async(once, is_retryable=is_retryable_gemini_error)

    async def generate(self, prompt: str) -> Generation:
        # No config on purpose: v1 called generate_content with the model defaults.
        response, latency_ms = await self._call(prompt, config=None)
        usage = response.usage_metadata
        return Generation(
            text=response.text or "",
            latency_ms=latency_ms,
            input_tokens=usage.prompt_token_count if usage else None,
            output_tokens=usage.candidates_token_count if usage else None,
        )

    async def generate_structured[M: BaseModel](self, prompt: str, schema: type[M]) -> M:
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, temperature=0.0
        )
        response, _ = await self._call(prompt, config=config)
        return schema.model_validate_json(response.text or "")
