"""Async Gemini client: bounded concurrency, request spacing, timeouts, retries on 429/5xx (with
the server's `retryDelay`, R8) and on network errors and timeouts (C-2), one log line per
attempt with latency, tokens and estimated cost."""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from dzdia_newsroom.llm.base import Generation, Structured, StructuredOutputError, Usage
from dzdia_newsroom.llm.pricing import estimate_cost_usd
from dzdia_newsroom.llm.retry import RequestSpacer, retry_async

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DEFAULT_TIMEOUT_S = 120.0
_DURATION = re.compile(r"(\d+(?:\.\d+)?)s")
_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"


def is_retryable_gemini_error(exc: Exception) -> bool:
    """Rate limits and server errors, plus transport failures: the SDK sends requests through
    httpx and lets its timeouts and connection errors through unwrapped (C-2)."""
    if isinstance(exc, errors.APIError):
        return exc.code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)  # includes every httpx timeout


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


def _usage(response: types.GenerateContentResponse) -> Usage:
    meta = response.usage_metadata
    if meta is None:
        return Usage()
    return Usage(meta.prompt_token_count, meta.candidates_token_count, meta.thoughts_token_count)


def _finish_reason(response: types.GenerateContentResponse) -> str | None:
    if not response.candidates or response.candidates[0].finish_reason is None:
        return None
    reason = response.candidates[0].finish_reason
    return reason.name if isinstance(reason, types.FinishReason) else str(reason)


class GeminiClient:
    """Implements TextGenerator and StructuredGenerator on top of google-genai."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_concurrency: int = 2,
        max_rpm: int = 20,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        thinking_level: str | None = None,
        retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.model = model
        # Structured calls only: `generate` keeps v1's defaults (ADR-0001, ADR-0008).
        self.thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000))
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._spacer = RequestSpacer(max_rpm)
        self._retry_sleep = retry_sleep

    async def _call(
        self, prompt: str, config: types.GenerateContentConfig | None
    ) -> tuple[types.GenerateContentResponse, int]:
        attempt = 0

        async def once() -> tuple[types.GenerateContentResponse, int]:
            nonlocal attempt
            attempt += 1
            async with self._semaphore:
                await self._spacer.wait()
                start = time.perf_counter()
                try:
                    response = await self._client.aio.models.generate_content(
                        model=self.model, contents=prompt, config=config
                    )
                except Exception as exc:
                    self._log_failure(exc, attempt, start)
                    raise
                latency_ms = int((time.perf_counter() - start) * 1000)
                self._log_success(response, attempt, latency_ms)
                return response, latency_ms

        return await retry_async(
            once,
            is_retryable=is_retryable_gemini_error,
            retry_delay=gemini_retry_delay,
            sleep=self._retry_sleep,
        )

    def _log_success(
        self, response: types.GenerateContentResponse, attempt: int, latency_ms: int
    ) -> None:
        usage = _usage(response)
        logger.info(
            "llm %s -> ok",
            self.model,
            extra={
                "model": self.model,
                "attempt": attempt,
                "latency_ms": latency_ms,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thinking_tokens": usage.thinking_tokens,
                "cost_usd": estimate_cost_usd(self.model, usage),
                "finish_reason": _finish_reason(response),
            },
        )

    def _log_failure(self, exc: Exception, attempt: int, start: float) -> None:
        logger.warning(
            "llm %s -> %s",
            self.model,
            type(exc).__name__,
            extra={
                "model": self.model,
                "attempt": attempt,
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "error": str(exc)[:300],
                "retryable": is_retryable_gemini_error(exc),
            },
        )

    async def generate(self, prompt: str, *, temperature: float | None = None) -> Generation:
        # No config when no temperature is given, on purpose: v1 called generate_content with
        # the model defaults. The `plain_prompt` arm of the v2 eval sends only a temperature
        # (ADR-0010, point 2).
        config = (
            types.GenerateContentConfig(temperature=temperature)
            if temperature is not None
            else None
        )
        response, latency_ms = await self._call(prompt, config=config)
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
        """Temperature 0, JSON constrained by `schema`. A schema with a union must not use a
        pydantic discriminator: the SDK rejects the `oneOf` it produces (spike V-18)."""
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
        usage = _usage(response)
        raw = response.text or ""
        try:
            value = schema.model_validate_json(raw)
        except ValidationError as exc:
            reason = _finish_reason(response)
            what = "empty response" if not raw.strip() else "output does not match the schema"
            raise StructuredOutputError(
                f"{what} (finish reason {reason}): {exc.error_count()} validation error(s)",
                raw_text=raw,
                finish_reason=reason,
                usage=usage,
                latency_ms=latency_ms,
            ) from exc
        return Structured(value, usage, latency_ms)
