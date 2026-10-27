import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.worker.queue import (
    Handler,
    PermanentJobError,
    Worker,
    WorkerConfig,
    retry_delay_s,
)

FAST = WorkerConfig(lease_s=1.0, heartbeat_s=0.01, poll_s=0.01, retry_base_s=5, retry_cap_s=300)


def make_job(kind: str = "echo", attempts: int = 1, max_attempts: int = 3) -> ClaimedJob:
    return ClaimedJob(
        id=1,
        kind=kind,
        payload={},
        attempts=attempts,
        max_attempts=max_attempts,
        claim_token=uuid4(),
    )


@dataclass
class FakeStore:
    """In-memory JobStore that records every call made by the worker."""

    jobs: list[ClaimedJob] = field(default_factory=list)
    lease_kept: bool = True
    extensions: int = 0
    completed: list[int] = field(default_factory=list)
    retried: list[tuple[int, float, str]] = field(default_factory=list)
    dead: list[tuple[int, str]] = field(default_factory=list)

    async def claim(self, lease_s: float) -> ClaimedJob | None:
        return self.jobs.pop(0) if self.jobs else None

    async def extend_lease(self, job: ClaimedJob, lease_s: float) -> bool:
        self.extensions += 1
        return self.lease_kept

    async def complete(self, job: ClaimedJob) -> bool:
        self.completed.append(job.id)
        return self.lease_kept

    async def retry_later(self, job: ClaimedJob, delay_s: float, error: str) -> bool:
        self.retried.append((job.id, delay_s, error))
        return True

    async def mark_dead(self, job: ClaimedJob, error: str) -> bool:
        self.dead.append((job.id, error))
        return True


def worker(store: FakeStore, handlers: dict[str, Handler]) -> Worker:
    return Worker(store, handlers, FAST, rand=lambda: 1.0)


async def eventually(condition: Callable[[], bool], timeout_s: float = 2.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not condition():
            await asyncio.sleep(0.001)


async def ok_handler(job: ClaimedJob) -> None:
    return None


async def failing_handler(job: ClaimedJob) -> None:
    raise ConnectionError("provider down")


# --- retry_delay_s ---------------------------------------------------------------------------


def test_retry_delay_doubles_then_caps() -> None:
    delays = [retry_delay_s(n, base_s=5, cap_s=30, rand=lambda: 1.0) for n in (1, 2, 3, 4, 5)]
    assert delays == [5, 10, 20, 30, 30]


def test_retry_delay_is_jittered_from_zero() -> None:
    assert retry_delay_s(3, base_s=5, cap_s=300, rand=lambda: 0.0) == 0
    assert retry_delay_s(3, base_s=5, cap_s=300, rand=lambda: 0.5) == 10


def test_config_rejects_heartbeat_not_shorter_than_lease() -> None:
    with pytest.raises(ValueError):
        WorkerConfig(lease_s=10, heartbeat_s=10)


# --- job outcomes ----------------------------------------------------------------------------


async def test_successful_job_is_completed() -> None:
    store = FakeStore(jobs=[make_job()])
    assert await worker(store, {"echo": ok_handler}).run_once()
    assert store.completed == [1] and not store.retried and not store.dead


async def test_empty_queue_returns_false() -> None:
    assert not await worker(FakeStore(), {}).run_once()


async def test_transient_failure_is_retried_with_backoff() -> None:
    store = FakeStore(jobs=[make_job(attempts=2)])
    await worker(store, {"echo": failing_handler}).run_once()
    assert store.retried == [(1, 10.0, "ConnectionError: provider down")]
    assert not store.completed and not store.dead


async def test_failure_on_last_attempt_is_dead() -> None:
    store = FakeStore(jobs=[make_job(attempts=3, max_attempts=3)])
    await worker(store, {"echo": failing_handler}).run_once()
    assert store.dead == [(1, "ConnectionError: provider down")] and not store.retried


async def test_permanent_error_is_dead_without_retry() -> None:
    async def refuse(job: ClaimedJob) -> None:
        raise PermanentJobError("user not allowed")

    store = FakeStore(jobs=[make_job(attempts=1)])
    await worker(store, {"echo": refuse}).run_once()
    assert store.dead == [(1, "PermanentJobError: user not allowed")] and not store.retried


async def test_unknown_kind_is_dead() -> None:
    store = FakeStore(jobs=[make_job(kind="mystery")])
    await worker(store, {"echo": ok_handler}).run_once()
    assert store.dead == [(1, "no handler for kind 'mystery'")]


async def test_job_reclaimed_after_last_attempt_is_dead_without_running() -> None:
    ran = False

    async def spy(job: ClaimedJob) -> None:
        nonlocal ran
        ran = True

    store = FakeStore(jobs=[make_job(attempts=4, max_attempts=3)])
    await worker(store, {"echo": spy}).run_once()
    assert not ran and store.dead == [(1, "lease expired during the last attempt")]


# --- lease and heartbeat ---------------------------------------------------------------------


async def test_heartbeat_extends_lease_while_handler_runs() -> None:
    async def slow(job: ClaimedJob) -> None:
        await asyncio.sleep(0.05)

    store = FakeStore(jobs=[make_job()])
    await worker(store, {"echo": slow}).run_once()
    assert store.extensions >= 2 and store.completed == [1]


async def test_lost_lease_cancels_handler_and_skips_completion() -> None:
    cancelled = asyncio.Event()

    async def long_running(job: ClaimedJob) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    store = FakeStore(jobs=[make_job()], lease_kept=False)
    await asyncio.wait_for(worker(store, {"echo": long_running}).run_once(), timeout=1)
    assert cancelled.is_set()
    assert not store.completed and not store.retried and not store.dead


async def test_worker_cancellation_also_cancels_the_handler() -> None:
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def long_running(job: ClaimedJob) -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    store = FakeStore(jobs=[make_job()])
    task = asyncio.create_task(worker(store, {"echo": long_running}).run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


# --- run_forever -----------------------------------------------------------------------------


async def test_run_forever_drains_and_wakes_on_event() -> None:
    store = FakeStore(jobs=[make_job(), make_job()])
    wake = asyncio.Event()
    task = asyncio.create_task(
        Worker(store, {"echo": ok_handler}, WorkerConfig(poll_s=30)).run_forever(wake)
    )
    try:
        await eventually(lambda: store.completed == [1, 1])
        store.jobs.append(make_job())
        wake.set()  # without the wake-up, the next poll would be 30 s away
        await eventually(lambda: store.completed == [1, 1, 1])
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
