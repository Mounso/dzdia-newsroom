"""SQL access to the source dossiers (ADR-0020, points 6 to 8): the `DossierStore` of
`tools/dossier.py` on Postgres, and the two purges.

Time comes from the database (`now()`). Every operation on a key first deletes that dossier if
it has expired, so the 48-hour rule holds whenever the purge runs (point 7). Nothing starts the
purges yet: the background task of the worker comes with the wiring.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any, Final

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from dzdia_newsroom.channels.base import INBOUND_MESSAGE_KIND
from dzdia_newsroom.tools.dossier import (
    DOSSIER_TTL,
    Decision,
    DossierKey,
    DossierState,
    Refusal,
    Source,
    SourceKind,
    StoreResult,
)

# Jobs whose payload holds a message text; emptied 48 hours after their last change (point 8).
JOB_PAYLOAD_TTL = timedelta(hours=48)

_KEY: Final = "channel = %(channel)s AND chat_id = %(chat_id)s"

_DROP_EXPIRED = "DELETE FROM dossiers WHERE " + _KEY + " AND last_activity_at < now() - %(ttl)s"

_SOURCE_COLUMNS: Final = (
    "s.position, s.kind, s.url, s.text, s.source_chars, s.truncated, s.paywall_suspected"
)

_STATE_OF_ID = """
SELECT count(*), coalesce(sum(length(text)), 0)::bigint,
       coalesce(array_agg(url) FILTER (WHERE url IS NOT NULL), '{}')
FROM dossier_sources WHERE dossier_id = %(dossier_id)s
"""

type Row = tuple[Any, ...]


def _source(row: Row) -> Source:
    position, kind, url, text, source_chars, truncated, paywall_suspected = row
    return Source(
        position=position,
        kind=SourceKind(kind),
        url=url,
        text=text,
        source_chars=source_chars,
        truncated=truncated,
        paywall_suspected=paywall_suspected,
    )


def _state(row: Row | None) -> DossierState:
    if row is None:
        return DossierState()
    count, total_chars, urls = row
    return DossierState(count=count, total_chars=total_chars, urls=frozenset(urls))


class DossierRepository:
    """Implements `DossierStore`. `ttl` is the dossier lifetime after its last activity and
    `job_payload_ttl` the age of a finished job whose payload is emptied; tests shorten them or
    move the timestamps by SQL."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[TupleRow]],
        *,
        ttl: timedelta = DOSSIER_TTL,
        job_payload_ttl: timedelta = JOB_PAYLOAD_TTL,
    ) -> None:
        self._pool = pool
        self._ttl = ttl
        self._job_payload_ttl = job_payload_ttl

    def _params(self, key: DossierKey, **params: object) -> dict[str, object]:
        return {"channel": key.channel, "chat_id": key.chat_id, "ttl": self._ttl, **params}

    async def add_source(
        self,
        key: DossierKey,
        *,
        message_id: str,
        link_index: int,
        decide: Callable[[DossierState], Decision],
    ) -> StoreResult:
        params = self._params(key, message_id=message_id, link_index=link_index)
        async with self._pool.connection() as conn, conn.transaction():
            # One upsert creates or locks the dossier row and returns it: the row lock serializes
            # the adds of one dossier, so the caps hold under concurrency, and no purge can
            # delete the row between finding and locking it.
            cursor = await conn.execute(
                "INSERT INTO dossiers (channel, chat_id) VALUES (%(channel)s, %(chat_id)s)"
                " ON CONFLICT (channel, chat_id) DO UPDATE SET channel = EXCLUDED.channel"
                " RETURNING id, last_activity_at < now() - %(ttl)s",
                params,
            )
            locked = await cursor.fetchone()
            if locked is None:  # never: an upsert with RETURNING always returns its row
                raise RuntimeError("the dossier upsert returned no row")
            dossier_id, expired = locked
            params["dossier_id"] = dossier_id
            if expired:
                # Dropped in place: its sources go and its row starts again, still locked.
                await conn.execute(
                    "DELETE FROM dossier_sources WHERE dossier_id = %(dossier_id)s", params
                )
                await conn.execute(
                    "UPDATE dossiers SET created_at = now(), last_activity_at = now()"
                    " WHERE id = %(dossier_id)s",
                    params,
                )

            cursor = await conn.execute(
                "SELECT "
                + _SOURCE_COLUMNS
                + " FROM dossier_sources s WHERE s.dossier_id = %(dossier_id)s"
                " AND s.message_id = %(message_id)s AND s.link_index = %(link_index)s",
                params,
            )
            replayed = await cursor.fetchone()
            if replayed is not None:
                return StoreResult(_source(replayed), await self._state_of(conn, params))

            decision = decide(await self._state_of(conn, params))
            if isinstance(decision, Refusal):
                return StoreResult(decision, await self._state_of(conn, params))

            cursor = await conn.execute(
                "INSERT INTO dossier_sources AS s (dossier_id, position, kind, url, text,"
                " source_chars, truncated, paywall_suspected, message_id, link_index)"
                " SELECT %(dossier_id)s, coalesce(max(position), 0) + 1, %(kind)s, %(url)s,"
                " %(text)s, %(source_chars)s, %(truncated)s, %(paywall_suspected)s,"
                " %(message_id)s, %(link_index)s"
                " FROM dossier_sources WHERE dossier_id = %(dossier_id)s"
                " RETURNING " + _SOURCE_COLUMNS,
                {
                    **params,
                    "kind": decision.kind.value,
                    "url": decision.url,
                    "text": decision.text,
                    "source_chars": decision.source_chars,
                    "truncated": decision.truncated,
                    "paywall_suspected": decision.paywall_suspected,
                },
            )
            inserted = await cursor.fetchone()
            assert inserted is not None
            await conn.execute(
                "UPDATE dossiers SET last_activity_at = now() WHERE id = %(dossier_id)s", params
            )
            return StoreResult(_source(inserted), await self._state_of(conn, params))

    @staticmethod
    async def _state_of(conn: AsyncConnection[TupleRow], params: dict[str, object]) -> DossierState:
        cursor = await conn.execute(_STATE_OF_ID, params)
        return _state(await cursor.fetchone())

    async def state(self, key: DossierKey) -> DossierState:
        params = self._params(key)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_DROP_EXPIRED, params)
            cursor = await conn.execute(
                "SELECT count(s.id), coalesce(sum(length(s.text)), 0)::bigint,"
                " coalesce(array_agg(s.url) FILTER (WHERE s.url IS NOT NULL), '{}')"
                " FROM dossiers d JOIN dossier_sources s ON s.dossier_id = d.id"
                " WHERE d.channel = %(channel)s AND d.chat_id = %(chat_id)s",
                params,
            )
            return _state(await cursor.fetchone())

    async def find_source(
        self, key: DossierKey, *, message_id: str, link_index: int
    ) -> Source | None:
        params = self._params(key, message_id=message_id, link_index=link_index)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_DROP_EXPIRED, params)
            cursor = await conn.execute(
                "SELECT " + _SOURCE_COLUMNS + " FROM dossiers d"
                " JOIN dossier_sources s ON s.dossier_id = d.id"
                " WHERE d.channel = %(channel)s AND d.chat_id = %(chat_id)s"
                " AND s.message_id = %(message_id)s AND s.link_index = %(link_index)s",
                params,
            )
            row = await cursor.fetchone()
            return None if row is None else _source(row)

    async def sources(self, key: DossierKey) -> list[Source]:
        params = self._params(key)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_DROP_EXPIRED, params)
            cursor = await conn.execute(
                "SELECT " + _SOURCE_COLUMNS + " FROM dossiers d"
                " JOIN dossier_sources s ON s.dossier_id = d.id"
                " WHERE d.channel = %(channel)s AND d.chat_id = %(chat_id)s"
                " ORDER BY s.position",
                params,
            )
            return [_source(row) for row in await cursor.fetchall()]

    async def touch(self, key: DossierKey) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE dossiers SET last_activity_at = now() WHERE "
                + _KEY
                + " AND last_activity_at >= now() - %(ttl)s",
                self._params(key),
            )

    async def clear(self, key: DossierKey) -> int:
        params = self._params(key)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_DROP_EXPIRED, params)
            cursor = await conn.execute(
                "SELECT id FROM dossiers WHERE " + _KEY + " FOR UPDATE", params
            )
            row = await cursor.fetchone()
            if row is None:
                return 0
            params["dossier_id"] = row[0]
            cursor = await conn.execute(
                "SELECT count(*) FROM dossier_sources WHERE dossier_id = %(dossier_id)s", params
            )
            counted = await cursor.fetchone()
            assert counted is not None
            await conn.execute("DELETE FROM dossiers WHERE id = %(dossier_id)s", params)
            removed: int = counted[0]
            return removed

    async def purge_expired(self) -> int:
        """Delete every dossier whose last activity is older than the TTL, with its sources;
        returns the number of dossiers deleted."""
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM dossiers WHERE last_activity_at < now() - %s", (self._ttl,)
            )
            return cursor.rowcount

    async def empty_old_job_payloads(self) -> int:
        """Replace by `{}` the payload of every `inbound_message` job that is `done` or `dead`
        and unchanged for `job_payload_ttl` (ADR-0020, point 8). The row, its `updated_at` and
        its `dedup_key` stay, so a redelivered webhook is still refused; queued and running jobs
        are never touched. Returns the number of payloads emptied."""
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE jobs SET payload = '{}'::jsonb"
                " WHERE kind = %(kind)s AND status IN ('done', 'dead')"
                " AND updated_at < now() - %(age)s AND payload <> '{}'::jsonb",
                {"kind": INBOUND_MESSAGE_KIND, "age": self._job_payload_ttl},
            )
            return cursor.rowcount
