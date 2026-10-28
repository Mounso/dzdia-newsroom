"""Job worker (ADR-004, option 2): short lease renewed by a heartbeat, fenced by a claim token,
retries with full-jitter exponential backoff, permanent errors never retried.

Delivery is at least once: a handler can run twice for the same job (e.g. a worker dies after
the side effect but before `complete`), so handlers must be idempotent or record their effects.
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import AsyncConnection, sql

from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.db.repository import JOBS_CHANNEL

logger = logging.getLogger(__name__)

Handler = Callable[[ClaimedJob], Coroutine[Any, Any, None]]


class PermanentJobError(Exception):
    """Raised by a handler when retrying cannot help (invalid input, refused user...)."""


class JobStore(Protocol):
    """What the worker needs from the queue; implemented by `db.repository.JobRepository`."""

    async def claim(self, lease_s: float) -> ClaimedJob | None: ...
    async def extend_lease(self, job: ClaimedJob, lease_s: float) -> bool: ...
    async def complete(self, job: ClaimedJob) -> bool: ...
    async def retry_later(self, job: ClaimedJob, delay_s: float, error: str) -> bool: ...
    async def mark_dead(self, job: ClaimedJob, error: str) -> bool: ...


@dataclass(frozen=True)
class WorkerConfig:
    lease_s: float = 60.0
    heartbeat_s: float = 20.0  # well below lease_s: two missed beats still keep the lease
    poll_s: float = 5.0  # fallback when a NOTIFY is missed
    retry_base_s: float = 5.0
    retry_cap_s: float = 300.0

    def __post_init__(self) -> None:
        if not 0 < self.heartbeat_s < self.lease_s:
            raise ValueError("heartbeat_s must be positive and shorter than lease_s")


DEFAULT_CONFIG = WorkerConfig()


def retry_delay_s(
    attempts: int, *, base_s: float, cap_s: float, rand: Callable[[], float] = random.random
) -> float:
    """Full jitter: uniform in [0, min(cap, base * 2^(attempts - 1))]. Spreads retries so that
    jobs failing together (e.g. a provider outage) do not all come back at the same instant."""
    return rand() * min(cap_s, base_s * 2.0 ** (attempts - 1))


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]


class Worker:
    def __init__(
        self,
        store: JobStore,
        handlers: Mapping[str, Handler],
        config: WorkerConfig = DEFAULT_CONFIG,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._store = store
        self._handlers = handlers
        self._config = config
        self._rand = rand

    async def run_forever(self, wake: asyncio.Event, stop: asyncio.Event | None = None) -> None:
        """Drain the queue, then sleep until `wake` is set (by NOTIFY) or `poll_s` elapses.

        Graceful stop: once `stop` is set, no new job is claimed and this returns after the
        current job ends. Cancelling the task stops at once (the job's lease then expires)."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            wake.clear()  # before draining: a NOTIFY arriving meanwhile is not lost
            try:
                while not stop.is_set() and await self.run_once():
                    pass
            except Exception:
                logger.exception("worker iteration failed; retrying after the poll interval")
            await self._sleep_until_set(wake, stop)

    async def _sleep_until_set(self, *events: asyncio.Event) -> None:
        waiters = {asyncio.create_task(event.wait()) for event in events}
        try:
            await asyncio.wait(
                waiters, timeout=self._config.poll_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waiters:
                waiter.cancel()

    async def run_once(self) -> bool:
        """Claim and process one job. Returns False when no job is ready."""
        job = await self._store.claim(self._config.lease_s)
        if job is None:
            return False
        await self._process(job)
        return True

    async def _process(self, job: ClaimedJob) -> None:
        log_ctx = f"job {job.id} ({job.kind}, attempt {job.attempts}/{job.max_attempts})"
        if job.attempts > job.max_attempts:
            # Only reachable when the lease of the last attempt expired (worker crash).
            await self._mark_dead(job, "lease expired during the last attempt", log_ctx)
            return
        handler = self._handlers.get(job.kind)
        if handler is None:
            await self._mark_dead(job, f"no handler for kind {job.kind!r}", log_ctx)
            return

        started = time.perf_counter()
        try:
            kept_lease = await self._run_with_heartbeat(job, handler)
        except PermanentJobError as exc:
            await self._mark_dead(job, _describe(exc), log_ctx)
            return
        except Exception as exc:
            await self._fail(job, exc, log_ctx)
            return
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not kept_lease:
            logger.warning("%s: lease lost after %d ms, handler cancelled", log_ctx, duration_ms)
        elif await self._store.complete(job):
            logger.info("%s: done in %d ms", log_ctx, duration_ms)
        else:
            logger.warning("%s: lease lost before completion (%d ms)", log_ctx, duration_ms)

    async def _fail(self, job: ClaimedJob, exc: Exception, log_ctx: str) -> None:
        if job.attempts >= job.max_attempts:
            await self._mark_dead(job, _describe(exc), log_ctx)
            return
        delay = retry_delay_s(
            job.attempts,
            base_s=self._config.retry_base_s,
            cap_s=self._config.retry_cap_s,
            rand=self._rand,
        )
        error = _describe(exc)
        if await self._store.retry_later(job, delay, error):
            logger.warning("%s: failed (%s), retry in %.1f s", log_ctx, error, delay)
        else:
            logger.warning("%s: failed (%s), lease lost: retry left to its owner", log_ctx, error)

    async def _mark_dead(self, job: ClaimedJob, reason: str, log_ctx: str) -> None:
        # A False return means another worker reclaimed the job: the row is no longer ours.
        if await self._store.mark_dead(job, reason):
            logger.error("%s: dead (%s)", log_ctx, reason)
        else:
            logger.warning("%s: lease lost before it could be marked dead (%s)", log_ctx, reason)

    async def _run_with_heartbeat(self, job: ClaimedJob, handler: Handler) -> bool:
        """Run the handler while renewing the lease. Returns False (handler cancelled) when the
        lease is lost; re-raises the handler's exception otherwise."""
        task = asyncio.create_task(handler(job))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=self._config.heartbeat_s)
                if not task.done() and not await self._store.extend_lease(
                    job, self._config.lease_s
                ):
                    task.cancel()
                    await asyncio.wait({task})
                    return False
        except BaseException:
            # Worker shutdown or heartbeat failure: never leave the handler running unowned.
            task.cancel()
            await asyncio.wait({task})
            raise
        task.result()
        return True


async def listen_for_jobs(
    conninfo: str,
    wake: asyncio.Event,
    *,
    connect_timeout_s: int = 10,
    max_backoff_s: float = 60.0,
) -> None:
    """Set `wake` on each NOTIFY of the jobs channel; reconnect with backoff on failure.
    The worker's polling covers the gaps while disconnected."""
    backoff_s = 1.0
    while True:
        try:
            async with await AsyncConnection.connect(
                conninfo, autocommit=True, connect_timeout=connect_timeout_s
            ) as conn:
                await conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(JOBS_CHANNEL)))
                logger.info("listening on channel %r", JOBS_CHANNEL)
                backoff_s = 1.0
                wake.set()  # jobs may have been enqueued while disconnected
                async for _ in conn.notifies():
                    wake.set()
        except Exception:
            logger.exception("LISTEN connection lost, reconnecting in %.0f s", backoff_s)
        await asyncio.sleep(backoff_s)
        backoff_s = min(max_backoff_s, backoff_s * 2)
