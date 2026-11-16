"""The dossier repository against a real Postgres (ADR-0020, points 5 to 8): the caps under the
row lock, replays, the cascade, expiry on read and the two purges."""

import asyncio
from datetime import timedelta

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from dzdia_newsroom.channels.base import INBOUND_MESSAGE_KIND
from dzdia_newsroom.db.dossiers import DossierRepository
from dzdia_newsroom.db.repository import JobRepository
from dzdia_newsroom.tools.dossier import (
    MAX_DOSSIER_CHARS,
    MAX_SOURCES,
    Candidate,
    Decision,
    DossierKey,
    DossierState,
    Refusal,
    RefusalReason,
    Source,
    SourceKind,
    decide,
)

Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]
Conn = AsyncConnection[TupleRow]

KEY = DossierKey(channel="telegram", chat_id="42")
OTHER_KEY = DossierKey(channel="telegram", chat_id="43")


def text_candidate(text: str = "t" * 500) -> Candidate:
    return Candidate(
        kind=SourceKind.PASTED_TEXT,
        url=None,
        text=text,
        source_chars=len(text),
        truncated=False,
        paywall_suspected=False,
    )


def link_candidate(url: str, text: str = "l" * 500) -> Candidate:
    return Candidate(
        kind=SourceKind.LINK,
        url=url,
        text=text,
        source_chars=len(text) + 10,
        truncated=True,
        paywall_suspected=True,
    )


async def add(
    repo: DossierRepository,
    candidate: Candidate,
    *,
    message_id: str,
    link_index: int = 0,
    key: DossierKey = KEY,
) -> Source | Refusal:
    def decision(state: DossierState) -> Decision:
        return decide(state, candidate)

    result = await repo.add_source(
        key, message_id=message_id, link_index=link_index, decide=decision
    )
    return result.outcome


async def scalar(conn: Conn, query: str, *params: object) -> object:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def age_dossier(conn: Conn, key: DossierKey, hours: float) -> None:
    await conn.execute(
        "UPDATE dossiers SET last_activity_at = now() - %s * interval '1 hour'"
        " WHERE channel = %s AND chat_id = %s",
        (hours, key.channel, key.chat_id),
    )


# --- add -------------------------------------------------------------------------------------


async def test_sources_are_stored_in_positions(pool: Pool) -> None:
    repo = DossierRepository(pool)
    first = await add(repo, link_candidate("https://a.org/x"), message_id="1")
    second = await add(repo, text_candidate(), message_id="2")
    assert first == Source(
        position=1,
        kind=SourceKind.LINK,
        url="https://a.org/x",
        text="l" * 500,
        source_chars=510,
        truncated=True,
        paywall_suspected=True,
    )
    assert isinstance(second, Source) and second.position == 2 and second.url is None
    assert await repo.sources(KEY) == [first, second]
    assert await repo.state(KEY) == DossierState(
        count=2, total_chars=1_000, urls=frozenset({"https://a.org/x"})
    )
    assert await repo.state(OTHER_KEY) == DossierState()


async def test_the_result_carries_the_state_after_the_add(pool: Pool) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    result = await repo.add_source(
        KEY, message_id="2", link_index=0, decide=lambda s: decide(s, text_candidate("u" * 300))
    )
    assert result.state == DossierState(count=2, total_chars=800)


async def test_a_link_already_there_is_refused(pool: Pool) -> None:
    repo = DossierRepository(pool)
    await add(repo, link_candidate("https://a.org/x"), message_id="1")
    refused = await add(repo, link_candidate("https://a.org/x"), message_id="2")
    assert refused == Refusal(RefusalReason.ALREADY_THERE)
    assert len(await repo.sources(KEY)) == 1


async def test_the_source_cap_holds_under_concurrent_adds(pool: Pool) -> None:
    repo = DossierRepository(pool)
    outcomes = await asyncio.gather(
        *(add(repo, text_candidate(), message_id=str(n)) for n in range(MAX_SOURCES + 2))
    )
    added = [outcome for outcome in outcomes if isinstance(outcome, Source)]
    assert sorted(source.position for source in added) == list(range(1, MAX_SOURCES + 1))
    assert outcomes.count(Refusal(RefusalReason.FULL)) == 2


async def test_the_character_cap_holds_under_concurrent_adds(pool: Pool) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate("a" * 30_000), message_id="1")
    await add(repo, text_candidate("b" * 20_000), message_id="2")
    outcomes = await asyncio.gather(
        add(repo, text_candidate("c" * 6_000), message_id="3"),
        add(repo, text_candidate("d" * 6_000), message_id="4"),
    )
    assert all(isinstance(outcome, Source) for outcome in outcomes)
    state = await repo.state(KEY)
    assert state.count == 4 and state.total_chars <= MAX_DOSSIER_CHARS
    assert sorted(source.truncated for source in await repo.sources(KEY)) == [
        False,
        False,
        False,
        True,
    ]


async def test_a_replay_adds_nothing_and_returns_the_stored_source(pool: Pool) -> None:
    repo = DossierRepository(pool)
    first = await add(repo, link_candidate("https://a.org/x"), message_id="7", link_index=1)
    calls: list[DossierState] = []

    def decision(state: DossierState) -> Decision:
        calls.append(state)
        return text_candidate()

    again = await repo.add_source(KEY, message_id="7", link_index=1, decide=decision)
    assert again.outcome == first and calls == []
    assert again.state.count == 1
    found = await repo.find_source(KEY, message_id="7", link_index=1)
    assert found == first
    assert await repo.find_source(KEY, message_id="7", link_index=0) is None


async def test_the_second_unique_key_backs_the_replay_rule(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="7")
    dossier_id = await scalar(conn, "SELECT id FROM dossiers")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await conn.execute(
            "INSERT INTO dossier_sources (dossier_id, position, kind, text, source_chars,"
            " truncated, paywall_suspected, message_id, link_index)"
            " VALUES (%s, 2, 'pasted_text', 'x', 1, false, false, '7', 0)",
            (dossier_id,),
        )


async def test_a_link_needs_its_url_and_a_text_has_none(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    dossier_id = await scalar(conn, "SELECT id FROM dossiers")
    for kind, url in (("link", None), ("pasted_text", "https://a.org")):
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                "INSERT INTO dossier_sources (dossier_id, position, kind, url, text,"
                " source_chars, truncated, paywall_suspected, message_id)"
                " VALUES (%s, 9, %s, %s, 'x', 1, false, false, 'z')",
                (dossier_id, kind, url),
            )


# --- activity, clear and expiry --------------------------------------------------------------


async def last_activity_age_s(conn: Conn, key: DossierKey = KEY) -> float:
    age = await scalar(
        conn,
        "SELECT extract(epoch FROM now() - last_activity_at)::float8 FROM dossiers"
        " WHERE channel = %s AND chat_id = %s",
        key.channel,
        key.chat_id,
    )
    assert isinstance(age, float)
    return age


async def test_adding_and_touching_are_activity_reading_is_not(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    await age_dossier(conn, KEY, 10)

    await repo.state(KEY)
    await repo.sources(KEY)
    await repo.find_source(KEY, message_id="1", link_index=0)
    assert await last_activity_age_s(conn) > 9 * 3600

    await repo.touch(KEY)
    assert await last_activity_age_s(conn) < 60

    await age_dossier(conn, KEY, 10)
    await add(repo, text_candidate(), message_id="2")
    assert await last_activity_age_s(conn) < 60


async def test_a_refused_add_is_not_activity(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, link_candidate("https://a.org/x"), message_id="1")
    await age_dossier(conn, KEY, 10)
    await add(repo, link_candidate("https://a.org/x"), message_id="2")
    assert await last_activity_age_s(conn) > 9 * 3600


async def test_clear_deletes_the_dossier_and_its_sources(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    await add(repo, text_candidate(), message_id="2")
    await add(repo, text_candidate(), message_id="1", key=OTHER_KEY)

    assert await repo.clear(KEY) == 2
    assert await scalar(conn, "SELECT count(*) FROM dossiers") == 1
    assert await scalar(conn, "SELECT count(*) FROM dossier_sources") == 1
    assert await repo.clear(KEY) == 0
    readded = await add(repo, text_candidate(), message_id="1")
    assert isinstance(readded, Source) and readded.position == 1


@pytest.mark.parametrize("read", ["state", "sources", "find_source", "clear"])
async def test_every_read_drops_an_expired_dossier(pool: Pool, conn: Conn, read: str) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    await add(repo, text_candidate(), message_id="1", key=OTHER_KEY)
    await age_dossier(conn, KEY, 49)

    match read:
        case "state":
            assert await repo.state(KEY) == DossierState()
        case "sources":
            assert await repo.sources(KEY) == []
        case "find_source":
            assert await repo.find_source(KEY, message_id="1", link_index=0) is None
        case _:
            assert await repo.clear(KEY) == 0
    assert await scalar(conn, "SELECT count(*) FROM dossiers") == 1  # the other key is kept
    assert await scalar(conn, "SELECT count(*) FROM dossier_sources") == 1


async def test_a_dossier_inside_its_ttl_is_kept(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    await age_dossier(conn, KEY, 47)
    assert (await repo.state(KEY)).count == 1


async def test_an_add_to_an_expired_dossier_starts_a_new_one(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, link_candidate("https://a.org/x"), message_id="1")
    await add(repo, text_candidate(), message_id="2")
    await age_dossier(conn, KEY, 49)

    again = await add(repo, link_candidate("https://a.org/x"), message_id="1")
    assert isinstance(again, Source) and again.position == 1
    assert (await repo.state(KEY)).count == 1


async def test_touch_does_not_revive_an_expired_dossier(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    await age_dossier(conn, KEY, 49)
    await repo.touch(KEY)
    assert await repo.state(KEY) == DossierState()


async def test_the_ttl_is_a_parameter(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool, ttl=timedelta(hours=1))
    await add(repo, text_candidate(), message_id="1")
    await age_dossier(conn, KEY, 2)
    assert await repo.state(KEY) == DossierState()


# --- purges ----------------------------------------------------------------------------------


async def test_purge_expired_deletes_only_expired_dossiers(pool: Pool, conn: Conn) -> None:
    repo = DossierRepository(pool)
    await add(repo, text_candidate(), message_id="1")
    await add(repo, text_candidate(), message_id="1", key=OTHER_KEY)
    await age_dossier(conn, KEY, 49)

    assert await repo.purge_expired() == 1
    assert await scalar(conn, "SELECT count(*) FROM dossier_sources") == 1
    assert await repo.purge_expired() == 0
    assert (await repo.state(OTHER_KEY)).count == 1


async def make_job(
    conn: Conn,
    jobs: JobRepository,
    dedup_key: str,
    *,
    status: str,
    hours_old: float,
    kind: str = INBOUND_MESSAGE_KIND,
    payload: dict[str, object] | None = None,
) -> int:
    job_id = await jobs.enqueue(
        kind,
        {"text": "un texte collé"} if payload is None else payload,  # type: ignore[arg-type]
        dedup_key=dedup_key,
    )
    assert job_id is not None
    await conn.execute(
        "UPDATE jobs SET status = %s, updated_at = now() - %s * interval '1 hour' WHERE id = %s",
        (status, hours_old, job_id),
    )
    return job_id


async def test_old_finished_job_payloads_are_emptied(pool: Pool, conn: Conn) -> None:
    repo, jobs = DossierRepository(pool), JobRepository(pool)
    done = await make_job(conn, jobs, "telegram:1", status="done", hours_old=49)
    dead = await make_job(conn, jobs, "telegram:2", status="dead", hours_old=49)
    kept = {
        await make_job(conn, jobs, "telegram:3", status="done", hours_old=47),
        await make_job(conn, jobs, "telegram:4", status="queued", hours_old=49),
        await make_job(conn, jobs, "telegram:5", status="running", hours_old=49),
        await make_job(conn, jobs, "other:6", status="done", hours_old=49, kind="echo"),
    }
    await make_job(conn, jobs, "telegram:7", status="done", hours_old=49, payload={})
    before = await scalar(conn, "SELECT array_agg(updated_at ORDER BY id) FROM jobs")

    assert await repo.empty_old_job_payloads() == 2

    cursor = await conn.execute("SELECT id, payload FROM jobs ORDER BY id")
    payloads: dict[object, object] = {row[0]: row[1] for row in await cursor.fetchall()}
    assert payloads[done] == {} and payloads[dead] == {}
    assert all(payloads[job_id] == {"text": "un texte collé"} for job_id in kept)
    assert await scalar(conn, "SELECT array_agg(updated_at ORDER BY id) FROM jobs") == before
    statuses = await scalar(conn, "SELECT array_agg(status ORDER BY id) FROM jobs")
    assert statuses == ["done", "dead", "done", "queued", "running", "done", "done"]
    assert await repo.empty_old_job_payloads() == 0


async def test_a_redelivered_update_is_still_refused_after_the_purge(
    pool: Pool, conn: Conn
) -> None:
    repo, jobs = DossierRepository(pool), JobRepository(pool)
    await make_job(conn, jobs, "telegram:900", status="done", hours_old=49)
    assert await repo.empty_old_job_payloads() == 1
    assert await jobs.enqueue(INBOUND_MESSAGE_KIND, {"text": "x"}, dedup_key="telegram:900") is None
