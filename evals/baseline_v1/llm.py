"""Thin async Gemini client for the baseline: bounded concurrency, request spacing, timeouts,
retries on 429/5xx that honor the server's `retryDelay` (R8)."""

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# A server asking to wait longer than this is treated as a hard limit (e.g. the daily quota):
# the call fails, and re-running the step retries only the failed records.
MAX_RETRY_DELAY_S = 120.0
_DURATION = re.compile(r"(\d+(?:\.\d+)?)s")
_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"


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


class TextGenerator(Protocol):
    model: str

    async def generate(self, prompt: str) -> Generation: ...


class StructuredGenerator(Protocol):
    model: str

    @property
    def thinking_level(self) -> str | None: ...

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]: ...


class RequestSpacer:
    """Spaces the start of successive requests by at least `60 / max_rpm` seconds, so a burst
    never exceeds the per-minute quota. Retries go through it too: they are requests."""

    def __init__(
        self,
        max_rpm: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_rpm < 1:
            raise ValueError("max_rpm must be at least 1")
        self.interval_s = 60.0 / max_rpm
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next_start - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_start = max(self._clock(), self._next_start) + self.interval_s


async def retry_async[T](
    call: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool],
    retry_delay: Callable[[Exception], float | None] = lambda _: None,
    max_retries: int = 4,
    base_delay_s: float = 2.0,
    max_retry_delay_s: float = MAX_RETRY_DELAY_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `call`, retrying retryable errors with exponential backoff and jitter.

    When the error carries a server-requested delay, wait at least that long (plus up to 10 %
    jitter, so concurrent callers do not retry in lockstep). A requested delay above
    `max_retry_delay_s` is not waited for: the error is raised.
    """
    attempt = 0
    while True:
        try:
            return await call()
        except Exception as exc:
            if attempt >= max_retries or not is_retryable(exc):
                raise
            backoff = min(60.0, base_delay_s * 2**attempt) + random.uniform(0, 1)
            requested = retry_delay(exc)
            if requested is not None and requested > max_retry_delay_s:
                raise
            if requested is not None:
                backoff = max(backoff, requested * (1 + 0.1 * random.random()))
            await sleep(backoff)
            attempt += 1


def is_retryable_gemini_error(exc: Exception) -> bool:
    return isinstance(exc, errors.APIError) and exc.code in RETRYABLE_STATUS


def gemini_retry_delay(exc: Exception) -> float | None:
    """The `retryDelay` of a `google.rpc.RetryInfo` detail (e.g. "17s"), in seconds."""
    if not isinstance(exc, errors.APIError) or not isinstance(exc.details, dict):
        return None
    error: Any = exc.details.get("error")
    details: Any = error.get("details") if isinstance(error, dict) else None
    for detail in details if isinstance(details, list) else []:
        if isinstance(detail, dict) and detail.get("@type") == _RETRY_INFO:
            match = _DURATION.fullmatch(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


class GeminiClient:
    """Implements TextGenerator and StructuredGenerator on top of google-genai."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_concurrency: int = 2,
        max_rpm: int = 20,
        timeout_s: float = 90.0,
        thinking_level: str | None = None,
    ) -> None:
        self.model = model
        # Structured calls only: `generate` keeps v1's defaults (ADR-0001, ADR-0008).
        self.thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._spacer = RequestSpacer(max_rpm)

    async def _call(
        self, prompt: str, config: types.GenerateContentConfig | None
    ) -> tuple[types.GenerateContentResponse, int]:
        async def once() -> tuple[types.GenerateContentResponse, int]:
            async with self._semaphore:
                await self._spacer.wait()
                start = time.perf_counter()
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                return response, int((time.perf_counter() - start) * 1000)

        return await retry_async(
            once, is_retryable=is_retryable_gemini_error, retry_delay=gemini_retry_delay
        )

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

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]:
        thinking = (
            types.ThinkingConfig(thinking_level=types.ThinkingLevel(self.thinking_level.upper()))
            if self.thinking_level is not None
            else None
        )
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
            thinking_config=thinking,
        )
        response, latency_ms = await self._call(prompt, config=config)
        meta = response.usage_metadata
        usage = (
            Usage(meta.prompt_token_count, meta.candidates_token_count, meta.thoughts_token_count)
            if meta
            else Usage()
        )
        return Structured(schema.model_validate_json(response.text or ""), usage, latency_ms)
