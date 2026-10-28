import asyncio

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from dzdia_newsroom.db.migrate import apply_migrations
from dzdia_newsroom.db.repository import JobRepository
from dzdia_newsroom.worker.queue import listen_for_jobs

Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]


async def status_of(conn: AsyncConnection[TupleRow], job_id: int) -> str:
    cursor = await conn.execute("SELECT status FROM jobs WHERE id = %s", (job_id,))
    row = await cursor.fetchone()
    assert row is not None
    status: str = row[0]
    return status


async def test_migrations_apply_once(conn: AsyncConnection[TupleRow]) -> None:
    assert await apply_migrations(conn) == ["0001_jobs"]
    assert await apply_migrations(conn) == []


async def test_enqueue_deduplicates_on_key(pool: Pool) -> None:
    repo = JobRepository(pool)
    first = await repo.enqueue("echo", {"text": "salam"}, dedup_key="telegram:42")
    assert first is not None
    assert await repo.enqueue("echo", {"text": "salam"}, dedup_key="telegram:42") is None


async def test_claim_takes_each_job_once(pool: Pool) -> None:
    repo = JobRepository(pool)
    job_id = await repo.enqueue("echo", {"text": "salam"})
    job = await repo.claim(lease_s=60)
    assert job is not None
    assert (job.id, job.kind, job.payload, job.attempts) == (job_id, "echo", {"text": "salam"}, 1)
    assert await repo.claim(lease_s=60) is None


async def test_concurrent_claims_get_distinct_jobs(pool: Pool) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    await repo.enqueue("echo", {})
    first, second = await asyncio.gather(repo.claim(60), repo.claim(60))
    assert first is not None and second is not None and first.id != second.id


async def test_complete_marks_done(pool: Pool, conn: AsyncConnection[TupleRow]) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    job = await repo.claim(60)
    assert job is not None
    assert await repo.complete(job)
    assert await status_of(conn, job.id) == "done"
    assert await repo.claim(60) is None


async def test_retry_later_delays_the_next_claim(pool: Pool) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    job = await repo.claim(60)
    assert job is not None
    assert await repo.retry_later(job, delay_s=3600, error="boom")
    assert await repo.claim(60) is None  # not before run_after

    await repo.enqueue("echo", {})
    other = await repo.claim(60)
    assert other is not None
    assert await repo.retry_later(other, delay_s=0, error="boom")
    again = await repo.claim(60)
    assert again is not None and again.id == other.id and again.attempts == 2


async def test_expired_lease_is_reclaimed_and_old_token_fenced(pool: Pool) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    stale = await repo.claim(lease_s=0)  # expires immediately: as if the worker had died
    assert stale is not None
    await asyncio.sleep(0.01)
    fresh = await repo.claim(lease_s=60)
    assert fresh is not None
    assert fresh.id == stale.id and fresh.claim_token != stale.claim_token
    assert fresh.attempts == 2
    # The first worker wakes up: none of its updates may touch the reclaimed job.
    assert not await repo.extend_lease(stale, 60)
    assert not await repo.complete(stale)
    assert not await repo.retry_later(stale, 0, "late")
    assert not await repo.mark_dead(stale, "late")
    assert not await repo.release(stale)
    assert await repo.complete(fresh)


async def test_extend_lease_keeps_job_owned(pool: Pool) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    job = await repo.claim(lease_s=0)
    assert job is not None
    assert await repo.extend_lease(job, 60)
    await asyncio.sleep(0.01)
    assert await repo.claim(60) is None  # lease renewed: not reclaimable


async def test_dead_job_is_never_claimed(pool: Pool, conn: AsyncConnection[TupleRow]) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    job = await repo.claim(60)
    assert job is not None
    assert await repo.mark_dead(job, "PermanentJobError: bad input")
    assert await status_of(conn, job.id) == "dead"
    assert await repo.claim(60) is None


async def test_release_requeues_at_once_and_returns_the_attempt(
    pool: Pool, conn: AsyncConnection[TupleRow]
) -> None:
    repo = JobRepository(pool)
    await repo.enqueue("echo", {})
    job = await repo.claim(60)
    assert job is not None and job.attempts == 1
    assert await repo.release(job)
    assert await status_of(conn, job.id) == "queued"
    again = await repo.claim(60)  # no lease to wait for
    assert again is not None and again.id == job.id
    assert again.attempts == 1  # the shutdown did not cost an attempt
    assert not await repo.release(job)  # old token: fenced


async def test_release_keeps_the_job_place_in_line(pool: Pool) -> None:
    repo = JobRepository(pool)
    first_id = await repo.enqueue("echo", {})
    first = await repo.claim(60)
    assert first is not None
    await repo.enqueue("echo", {})
    assert await repo.release(first)
    again = await repo.claim(60)
    assert again is not None and again.id == first_id  # not behind the newer job


async def test_enqueue_notifies_listeners(pool: Pool, database_url: str) -> None:
    wake = asyncio.Event()
    listener = asyncio.create_task(listen_for_jobs(database_url, wake))
    try:
        await asyncio.wait_for(wake.wait(), timeout=10)  # set once LISTEN is active
        wake.clear()
        await JobRepository(pool).enqueue("echo", {})
        await asyncio.wait_for(wake.wait(), timeout=5)
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
