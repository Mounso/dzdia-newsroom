"""Provider-neutral retry helper and request spacing."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

# A server asking to wait longer than this is treated as a hard limit (e.g. the daily quota):
# the call fails, and re-running the step retries only the failed records.
MAX_RETRY_DELAY_S = 120.0


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
