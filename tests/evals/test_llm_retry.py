from typing import Any

import pytest
from google.genai import errors

from evals.baseline_v1.llm import RequestSpacer, gemini_retry_delay, retry_async


class Transient(Exception):
    pass


async def _no_sleep(_: float) -> None:
    return None


async def test_retry_succeeds_after_transient_failures() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Transient
        return "ok"

    result = await retry_async(
        flaky, is_retryable=lambda e: isinstance(e, Transient), sleep=_no_sleep
    )
    assert result == "ok" and attempts == 3


async def test_retry_gives_up_after_max_retries() -> None:
    attempts = 0

    async def always_fails() -> str:
        nonlocal attempts
        attempts += 1
        raise Transient

    with pytest.raises(Transient):
        await retry_async(always_fails, is_retryable=lambda e: True, max_retries=2, sleep=_no_sleep)
    assert attempts == 3


async def test_non_retryable_error_is_raised_immediately() -> None:
    attempts = 0

    async def broken() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        await retry_async(broken, is_retryable=lambda e: False, sleep=_no_sleep)
    assert attempts == 1


def _quota_error(retry_delay: str | None) -> errors.ClientError:
    details: list[dict[str, Any]] = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure"}]
    if retry_delay is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return errors.ClientError(429, body)


@pytest.mark.parametrize(("raw", "expected"), [("17s", 17.0), ("0.5s", 0.5), ("soon", None)])
def test_gemini_retry_delay_reads_retry_info(raw: str, expected: float | None) -> None:
    assert gemini_retry_delay(_quota_error(raw)) == expected


def test_gemini_retry_delay_is_none_without_retry_info() -> None:
    assert gemini_retry_delay(_quota_error(None)) is None
    assert gemini_retry_delay(errors.ServerError(503, {"error": "unavailable"})) is None
    assert gemini_retry_delay(ValueError("not an API error")) is None


async def test_retry_waits_at_least_the_requested_delay() -> None:
    waits: list[float] = []

    async def record(delay: float) -> None:
        waits.append(delay)

    attempts = 0

    async def limited() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise Transient
        return "ok"

    result = await retry_async(
        limited, is_retryable=lambda e: True, retry_delay=lambda e: 30.0, sleep=record
    )
    assert result == "ok"
    assert len(waits) == 1 and 30.0 <= waits[0] <= 33.0  # up to 10 % jitter


async def test_requested_delay_above_the_cap_is_not_waited_for() -> None:
    async def daily_quota() -> str:
        raise Transient

    with pytest.raises(Transient):
        await retry_async(
            daily_quota,
            is_retryable=lambda e: True,
            retry_delay=lambda e: 3600.0,
            max_retry_delay_s=120.0,
            sleep=_no_sleep,
        )


async def test_spacer_spaces_request_starts() -> None:
    now = 100.0
    waits: list[float] = []

    async def advance(delay: float) -> None:
        nonlocal now
        waits.append(delay)
        now += delay

    spacer = RequestSpacer(20, clock=lambda: now, sleep=advance)  # one start every 3 s
    starts = []
    for _ in range(3):
        await spacer.wait()
        starts.append(now)
    assert starts == [100.0, 103.0, 106.0]
    assert waits == [3.0, 3.0]


async def test_spacer_does_not_wait_after_an_idle_period() -> None:
    now = 0.0

    async def fail(_: float) -> None:
        raise AssertionError("should not wait")

    spacer = RequestSpacer(20, clock=lambda: now, sleep=fail)
    await spacer.wait()
    now = 10.0  # longer than the 3 s interval
    await spacer.wait()


def test_spacer_rejects_a_zero_rate() -> None:
    with pytest.raises(ValueError):
        RequestSpacer(0)
