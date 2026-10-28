"""SQL access to Postgres. Connections come from a pool opened in autocommit mode."""

from typing import Final, LiteralString

from psycopg import AsyncConnection
from psycopg.rows import TupleRow, class_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import JsonValue

from dzdia_newsroom.db.models import ClaimedJob

JOBS_CHANNEL = "jobs"

_CLAIM_SQL = """
UPDATE jobs
SET status = 'running',
    attempts = attempts + 1,
    claim_token = gen_random_uuid(),
    locked_until = now() + %(lease_s)s::float8 * interval '1 second',
    updated_at = now()
WHERE id = (
    SELECT id FROM jobs
    WHERE (status = 'queued' AND run_after <= now())
       OR (status = 'running' AND locked_until < now())  -- lease expired: worker presumed dead
    ORDER BY run_after, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, kind, payload, attempts, max_attempts, claim_token
"""

# Every update of a claimed job is fenced by its claim token: a worker that lost its lease
# (and whose job was reclaimed by another worker) can no longer change it.
_OWNED: Final = "WHERE id = %(id)s AND claim_token = %(token)s AND status = 'running'"


class JobRepository:
    def __init__(self, pool: AsyncConnectionPool[AsyncConnection[TupleRow]]) -> None:
        self._pool = pool

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, JsonValue],
        *,
        dedup_key: str | None = None,
        max_attempts: int = 3,
    ) -> int | None:
        """Insert a job and wake the workers. Returns None when `dedup_key` already exists."""
        async with self._pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                "INSERT INTO jobs (kind, payload, dedup_key, max_attempts)"
                " VALUES (%s, %s, %s, %s)"
                " ON CONFLICT (dedup_key) DO NOTHING RETURNING id",
                (kind, Jsonb(payload), dedup_key, max_attempts),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            # Delivered at commit, so a woken worker always sees the new row.
            await conn.execute("SELECT pg_notify(%s, %s)", (JOBS_CHANNEL, kind))
            job_id: int = row[0]
            return job_id

    async def claim(self, lease_s: float) -> ClaimedJob | None:
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=class_row(ClaimedJob)) as cursor,
        ):
            await cursor.execute(_CLAIM_SQL, {"lease_s": lease_s})
            return await cursor.fetchone()

    async def extend_lease(self, job: ClaimedJob, lease_s: float) -> bool:
        """Returns False when the lease was lost: the caller must stop working on the job."""
        return await self._update_owned(
            "UPDATE jobs SET locked_until = now() + %(lease_s)s::float8 * interval '1 second',"
            " updated_at = now() " + _OWNED,
            job,
            lease_s=lease_s,
        )

    async def complete(self, job: ClaimedJob) -> bool:
        return await self._update_owned(
            "UPDATE jobs SET status = 'done', locked_until = NULL, updated_at = now() " + _OWNED,
            job,
        )

    async def retry_later(self, job: ClaimedJob, delay_s: float, error: str) -> bool:
        return await self._update_owned(
            "UPDATE jobs SET status = 'queued', locked_until = NULL, claim_token = NULL,"
            " run_after = now() + %(delay_s)s::float8 * interval '1 second',"
            " last_error = %(error)s, updated_at = now() " + _OWNED,
            job,
            delay_s=delay_s,
            error=error,
        )

    async def mark_dead(self, job: ClaimedJob, error: str) -> bool:
        return await self._update_owned(
            "UPDATE jobs SET status = 'dead', locked_until = NULL,"
            " last_error = %(error)s, updated_at = now() " + _OWNED,
            job,
            error=error,
        )

    async def release(self, job: ClaimedJob) -> bool:
        """Give the job back at worker shutdown: claimable again at once, in its old place
        (`run_after` unchanged), and the attempt taken by this claim is returned."""
        return await self._update_owned(
            "UPDATE jobs SET status = 'queued', attempts = attempts - 1, locked_until = NULL,"
            " claim_token = NULL, updated_at = now() " + _OWNED,
            job,
        )

    async def _update_owned(self, query: LiteralString, job: ClaimedJob, **params: object) -> bool:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(query, {"id": job.id, "token": job.claim_token, **params})
            return cursor.rowcount == 1
