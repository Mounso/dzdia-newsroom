"""The retention task of the worker process (ADR-0020, points 7 and 8): at start, then every
hour, delete the expired dossiers and empty the payload of old finished jobs.

A failed run is logged and tried again at the next hour. The queue is not changed and nothing is
added to the host: the task lives in the worker process.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NoReturn, Protocol

logger = logging.getLogger(__name__)

RETENTION_INTERVAL_S = 3600.0
# Bounds one run, so that a stuck database call cannot stop the next hours' runs.
RUN_TIMEOUT_S = 60.0


class RetentionStore(Protocol):
    """Implemented by `db.dossiers.DossierRepository`."""

    async def purge_expired(self) -> int: ...

    async def empty_old_job_payloads(self) -> int: ...


async def run_once(store: RetentionStore, *, timeout_s: float = RUN_TIMEOUT_S) -> None:
    """One run: both purges, one INFO line with the two counts. Raises on failure."""
    async with asyncio.timeout(timeout_s):
        dossiers = await store.purge_expired()
        payloads = await store.empty_old_job_payloads()
    logger.info(
        "retention: %d expired dossier(s) deleted, %d job payload(s) emptied",
        dossiers,
        payloads,
        extra={"dossiers_deleted": dossiers, "job_payloads_emptied": payloads},
    )


async def run_retention(
    store: RetentionStore,
    *,
    interval_s: float = RETENTION_INTERVAL_S,
    timeout_s: float = RUN_TIMEOUT_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> NoReturn:
    """Run at once, then every `interval_s`, until cancelled. A failed run is logged with its
    exception and the loop carries on."""
    while True:
        try:
            await run_once(store, timeout_s=timeout_s)
        except Exception:
            logger.exception("retention run failed; next run in %g s", interval_s)
        await sleep(interval_s)
