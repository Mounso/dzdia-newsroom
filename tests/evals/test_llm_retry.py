import pytest

from evals.baseline_v1.llm import retry_async


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
