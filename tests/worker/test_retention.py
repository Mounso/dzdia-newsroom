"""The retention task of the worker (ADR-0020, points 7 and 8): both purges at start, then every
interval; a failed run is logged and the loop carries on. No database: a fake store."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import pytest

from dzdia_newsroom.worker.retention import (
    RETENTION_INTERVAL_S,
    run_once,
    run_retention,
)

LOGGER = "dzdia_newsroom.worker.retention"


@dataclass
class FakeRetentionStore:
    """Each run takes the next outcome: a pair of counts, or an exception to raise."""

    outcomes: list[tuple[int, int] | Exception]
    calls: list[str] = field(default_factory=list)
    hang: bool = False

    async def purge_expired(self) -> int:
        self.calls.append("purge_expired")
        if self.hang:
            await asyncio.sleep(30)
        outcome = self.outcomes[0]
        if isinstance(outcome, Exception):
            self.outcomes.pop(0)
            raise outcome
        return outcome[0]

    async def empty_old_job_payloads(self) -> int:
        self.calls.append("empty_old_job_payloads")
        outcome = self.outcomes.pop(0)
        assert not isinstance(outcome, Exception)
        return outcome[1]


class Stop(Exception):
    """Ends the loop from the injected sleep."""


def sleeper(runs: int, slept: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= runs:
            raise Stop

    return sleep


def records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER]


async def test_a_run_purges_both_and_logs_the_counts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    store = FakeRetentionStore([(2, 5)])
    await run_once(store)
    assert store.calls == ["purge_expired", "empty_old_job_payloads"]
    [record] = records(caplog)
    assert record.levelno == logging.INFO
    assert (record.dossiers_deleted, record.job_payloads_emptied) == (2, 5)  # type: ignore[attr-defined]


async def test_the_loop_runs_at_start_then_every_interval(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    store = FakeRetentionStore([(1, 0), (0, 3), (0, 0)])
    slept: list[float] = []
    with pytest.raises(Stop):
        await run_retention(store, interval_s=7.0, sleep=sleeper(3, slept))
    assert slept == [7.0, 7.0, 7.0]
    assert store.calls.count("purge_expired") == 3
    assert len(records(caplog)) == 3


async def test_the_default_interval_is_one_hour() -> None:
    assert RETENTION_INTERVAL_S == 3600.0


async def test_a_failed_run_is_logged_and_the_loop_carries_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    store = FakeRetentionStore([RuntimeError("database down"), (4, 1)])
    slept: list[float] = []
    with pytest.raises(Stop):
        await run_retention(store, interval_s=1.0, sleep=sleeper(2, slept))
    failed, done = records(caplog)
    assert failed.levelno == logging.ERROR and failed.exc_info is not None
    assert done.levelno == logging.INFO
    assert store.calls == ["purge_expired", "purge_expired", "empty_old_job_payloads"]


async def test_a_stuck_run_times_out_and_the_loop_carries_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    store = FakeRetentionStore([(0, 0)], hang=True)
    slept: list[float] = []
    with pytest.raises(Stop):
        await run_retention(
            store,
            interval_s=1.0,
            timeout_s=0.05,
            sleep=sleeper(1, slept),
        )
    [failed] = records(caplog)
    assert failed.levelno == logging.ERROR
    assert slept == [1.0]


async def test_cancellation_stops_the_loop() -> None:
    store = FakeRetentionStore([(0, 0)] * 10)
    task = asyncio.create_task(run_retention(store, interval_s=3600.0))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
