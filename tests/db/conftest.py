"""Integration fixtures: a real Postgres from TEST_DATABASE_URL (skipped when unset).

Each test runs in its own throwaway schema, dropped afterwards, so the target database is
never modified outside it.
"""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from dzdia_newsroom.db.migrate import apply_migrations


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
async def schema(database_url: str) -> AsyncIterator[str]:
    name = f"test_{uuid4().hex[:12]}"
    async with await AsyncConnection.connect(database_url, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            yield name
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
async def conn(database_url: str, schema: str) -> AsyncIterator[AsyncConnection[TupleRow]]:
    async with await AsyncConnection.connect(
        database_url, autocommit=True, options=f"-c search_path={schema}"
    ) as connection:
        yield connection


@pytest.fixture
async def pool(
    database_url: str, schema: str, conn: AsyncConnection[TupleRow]
) -> AsyncIterator[AsyncConnectionPool[AsyncConnection[TupleRow]]]:
    await apply_migrations(conn)
    async with AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=4,
        kwargs={"autocommit": True, "options": f"-c search_path={schema}"},
        open=False,
    ) as p:
        await p.wait(timeout=10)
        yield p
