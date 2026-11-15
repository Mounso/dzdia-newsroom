"""End to end against a real Postgres: webhook -> jobs table -> worker -> channel reply."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from dzdia_newsroom.api.app import create_app
from dzdia_newsroom.api.webhook import SECRET_HEADER, WEBHOOK_PATH, AllowPolicy, WebhookConfig
from dzdia_newsroom.db.repository import JobRepository
from dzdia_newsroom.llm import FakeLLM
from dzdia_newsroom.worker.handlers import build_handlers
from dzdia_newsroom.worker.queue import Worker
from dzdia_newsroom.worker.replies import HELP_TEXT
from tests.telegram_updates import ALLOWED_USER_ID, make_update
from tests.worker.test_handlers import FakeChannel

SECRET = "s" * 40


async def test_update_is_queued_once_and_answered(
    pool: AsyncConnectionPool[AsyncConnection[TupleRow]], conn: AsyncConnection[TupleRow]
) -> None:
    repository = JobRepository(pool)

    @asynccontextmanager
    async def open_jobs() -> AsyncIterator[JobRepository]:
        yield repository

    policy = AllowPolicy(user_ids=frozenset({str(ALLOWED_USER_ID)}))
    app = create_app(WebhookConfig(secret=SECRET, policy=policy), open_jobs)
    update = make_update(update_id=900)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://api") as client,
    ):
        for _ in range(2):  # Telegram redelivers the same update
            response = await client.post(WEBHOOK_PATH, json=update, headers={SECRET_HEADER: SECRET})
            assert response.status_code == 200

    cursor = await conn.execute("SELECT kind, dedup_key, status FROM jobs")
    assert await cursor.fetchall() == [("inbound_message", "telegram:900", "queued")]

    channel = FakeChannel()

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a short private message must not fetch anything")

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as pages:
        worker = Worker(repository, build_handlers(channel, pages, FakeLLM([])))
        assert await worker.run_once()
        assert not await worker.run_once()
    # "Bonjour" in a private chat gets the help text (ADR-0019, point 3).
    assert channel.sent == [(str(ALLOWED_USER_ID), HELP_TEXT, "42")]

    cursor = await conn.execute("SELECT status FROM jobs")
    assert await cursor.fetchall() == [("done",)]
