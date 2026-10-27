"""Connection pool shared by `api` and `worker`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def open_pool(
    conninfo: str, *, max_size: int = 4, timeout_s: float = 10.0
) -> AsyncIterator[AsyncConnectionPool[AsyncConnection[TupleRow]]]:
    """Open an autocommit pool (what `JobRepository` expects) and fail fast if the database
    is unreachable at startup."""
    async with AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        timeout=timeout_s,
        kwargs={"autocommit": True, "connect_timeout": int(timeout_s)},
        open=False,
    ) as pool:
        await pool.wait(timeout=timeout_s)
        yield pool
