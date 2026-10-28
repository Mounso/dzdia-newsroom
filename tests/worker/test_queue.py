import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.worker.queue import (
    Handler,
    PermanentJobError,
    RetryLaterJobError,
    Worker,
    WorkerConfig,
    retry_delay_s,
)

FAST = WorkerConfig(
    lease_s=1.0,
    heartbeat_s=0.01,
    poll_s=0.01,
    retry_base_s=5,
    retry_cap_s=300,
    max_retry_after_s=3600,
    retry_after_jitter=0.1,
    release_timeout_s=0.05,
)


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
    released: list[int] = field(default_factory=list)
    release_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_stall_s: float = 0.0  # a slow database at shutdown
    release_owned: bool = True  # False: the job was reclaimed just before the shutdown

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
        return self.lease_kept

    async def mark_dead(self, job: ClaimedJob, error: str) -> bool:
        self.dead.append((job.id, error))
        return self.lease_kept

    async def release(self, job: ClaimedJob) -> bool:
        self.release_started.set()
        await asyncio.sleep(self.release_stall_s)
        self.released.append(job.id)
        return self.release_owned


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


@pytest.mark.parametrize("attempts", [1, 3])
async def test_failure_after_lease_loss_is_not_logged_as_handled(
    attempts: int, caplog: pytest.LogCaptureFixture
) -> None:
    # The job was reclaimed by another worker meanwhile: our retry/dead update matched no row.
    store = FakeStore(jobs=[make_job(attempts=attempts, max_attempts=3)], lease_kept=False)
    await worker(store, {"echo": failing_handler}).run_once()
    assert "lease lost" in caplog.text and "retry in" not in caplog.text
    assert all(record.levelname != "ERROR" for record in caplog.records)  # no "dead" claim


async def test_permanent_error_is_dead_without_retry() -> None:
    async def refuse(job: ClaimedJob) -> None:
        raise PermanentJobError("user not allowed")

    store = FakeStore(jobs=[make_job(attempts=1)])
    await worker(store, {"echo": refuse}).run_once()
    assert store.dead == [(1, "PermanentJobError: user not allowed")] and not store.retried


def rate_limited(delay_s: float) -> Handler:
    async def handler(job: ClaimedJob) -> None:
        raise RetryLaterJobError(f"rate limited for {delay_s:.0f} s", delay_s=delay_s)

    return handler


async def test_retry_after_is_waited_with_jitter_after_it() -> None:
    # Backoff alone would retry in 5 s, inside the rate-limit window.
    store = FakeStore(jobs=[make_job(attempts=1)])
    await worker(store, {"echo": rate_limited(120)}).run_once()
    [(job_id, delay, error)] = store.retried
    assert (job_id, error) == (1, "RetryLaterJobError: rate limited for 120 s")
    assert delay == pytest.approx(132.0)  # 120 s plus the 10 % jitter (rand = 1)


async def test_short_retry_after_does_not_shorten_the_backoff() -> None:
    store = FakeStore(jobs=[make_job(attempts=2)])
    await worker(store, {"echo": rate_limited(1)}).run_once()
    assert [delay for _, delay, _ in store.retried] == [10.0]


async def test_retry_after_over_the_limit_is_dead() -> None:
    store = FakeStore(jobs=[make_job(attempts=1)])
    await worker(store, {"echo": rate_limited(7200)}).run_once()
    assert not store.retried
    assert store.dead == [
        (1, "RetryLaterJobError: rate limited for 7200 s (retry-after over 3600 s)")
    ]


async def test_retry_after_on_last_attempt_is_dead() -> None:
    store = FakeStore(jobs=[make_job(attempts=3, max_attempts=3)])
    await worker(store, {"echo": rate_limited(60)}).run_once()
    assert store.dead == [(1, "RetryLaterJobError: rate limited for 60 s")] and not store.retried


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


async def start_long_job(store: FakeStore) -> tuple["asyncio.Task[bool]", asyncio.Event]:
    """Run a 10 s job on `store`; return the worker task once the handler runs, and a flag
    set when the handler is cancelled."""
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def long_running(job: ClaimedJob) -> None:
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(worker(store, {"echo": long_running}).run_once())
    await started.wait()
    return task, cancelled


async def assert_cancelled(task: "asyncio.Task[bool]") -> None:
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


async def test_worker_cancellation_cancels_the_handler_and_releases_the_job(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="dzdia_newsroom.worker.queue")
    store = FakeStore(jobs=[make_job()])
    task, cancelled = await start_long_job(store)
    task.cancel()
    await assert_cancelled(task)
    assert cancelled.is_set()
    assert store.released == [1]
    assert not store.completed and not store.retried and not store.dead
    assert "released at shutdown" in caplog.text


async def test_release_survives_a_second_cancel() -> None:
    store = FakeStore(jobs=[make_job()], release_stall_s=0.05)
    task, _ = await start_long_job(store)
    task.cancel()
    await store.release_started.wait()
    task.cancel()  # e.g. Ctrl+C twice, or the caller's own cleanup
    await assert_cancelled(task)
    await eventually(lambda: store.released == [1])  # the shielded update still lands


async def test_release_after_lease_loss_is_not_logged_as_released(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(jobs=[make_job()], release_owned=False)
    task, _ = await start_long_job(store)
    task.cancel()
    await assert_cancelled(task)
    assert "lease lost before it could be released" in caplog.text
    assert "released at shutdown" not in caplog.text


async def test_stalled_release_does_not_hold_up_the_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(jobs=[make_job()], release_stall_s=0.5)
    task, _ = await start_long_job(store)
    task.cancel()
    async with asyncio.timeout(0.4):  # bounded by release_timeout_s (0.05 s), not the stall
        await assert_cancelled(task)
    assert not store.released
    assert "release failed at shutdown" in caplog.text
    await eventually(lambda: store.released == [1])  # let the shielded update end cleanly


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


async def test_stop_while_idle_returns_without_waiting_for_the_poll() -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(
        Worker(FakeStore(), {}, WorkerConfig(poll_s=30)).run_forever(asyncio.Event(), stop)
    )
    await asyncio.sleep(0.01)  # let it go idle
    stop.set()
    await asyncio.wait_for(task, timeout=1)


async def test_stop_lets_the_current_job_finish_and_claims_no_more() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(job: ClaimedJob) -> None:
        started.set()
        await release.wait()

    store = FakeStore(jobs=[make_job(), make_job()])
    stop = asyncio.Event()
    task = asyncio.create_task(worker(store, {"echo": slow}).run_forever(asyncio.Event(), stop))
    await started.wait()
    stop.set()
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert store.completed == [1]  # the running job finished
    assert len(store.jobs) == 1  # the second one was left in the queue
