"""End to end against a real Postgres: webhook -> jobs table -> worker -> channel reply, with the
dossier of a private chat in its tables (ADR-0020)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from dzdia_newsroom.api.app import create_app
from dzdia_newsroom.api.webhook import SECRET_HEADER, WEBHOOK_PATH, AllowPolicy, WebhookConfig
from dzdia_newsroom.db.dossiers import DossierRepository
from dzdia_newsroom.db.repository import JobRepository
from dzdia_newsroom.llm import FakeLLM
from dzdia_newsroom.tools.article import SentenceSegment
from dzdia_newsroom.tools.dossier import DossierState
from dzdia_newsroom.tools.dossier_article import DossierArticle, HeadedParagraph
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.handlers import build_handlers
from dzdia_newsroom.worker.queue import Worker
from tests.telegram_updates import ALLOWED_USER_ID, make_update
from tests.worker.test_handlers import FakeChannel

SECRET = "s" * 40
Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]
CHAT = str(ALLOWED_USER_ID)
PASTED = (
    "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre régional, devant un public "
    "nombreux venu de toute la région.\n"
    "Douze films sont en compétition cette année, dont quatre premières oeuvres, selon les "
    "organisateurs qui annoncent aussi des ateliers pour les jeunes réalisateurs.\n"
    "La clôture est prévue le 10 mai avec la remise des prix par le jury international."
)


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError("these messages must not fetch anything")


async def post_updates(repository: JobRepository, *updates: dict[str, Any]) -> None:
    @asynccontextmanager
    async def open_jobs() -> AsyncIterator[JobRepository]:
        yield repository

    policy = AllowPolicy(user_ids=frozenset({CHAT}))
    app = create_app(WebhookConfig(secret=SECRET, policy=policy), open_jobs)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://api") as client,
    ):
        for update in updates:
            response = await client.post(WEBHOOK_PATH, json=update, headers={SECRET_HEADER: SECRET})
            assert response.status_code == 200


async def test_update_is_queued_once_and_answered(
    pool: Pool, conn: AsyncConnection[TupleRow]
) -> None:
    repository = JobRepository(pool)
    update = make_update(update_id=900)
    await post_updates(repository, update, update)  # Telegram redelivers the same update

    cursor = await conn.execute("SELECT kind, dedup_key, status FROM jobs")
    assert await cursor.fetchall() == [("inbound_message", "telegram:900", "queued")]

    channel = FakeChannel()
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as pages:
        handlers = build_handlers(channel, pages, FakeLLM([]), DossierRepository(pool))
        worker = Worker(repository, handlers)
        assert await worker.run_once()
        assert not await worker.run_once()
    # "Bonjour" in a private chat gets the help text (ADR-0020, point 2).
    assert channel.sent == [(CHAT, replies.help_text(DossierState()), "42")]

    cursor = await conn.execute("SELECT status FROM jobs")
    assert await cursor.fetchall() == [("done",)]


async def test_a_pasted_text_then_ecris_gives_a_receipt_then_two_messages(
    pool: Pool, conn: AsyncConnection[TupleRow]
) -> None:
    repository = JobRepository(pool)
    pasted = make_update(update_id=901, text=PASTED)
    write = make_update(update_id=902, text="/ecris")
    write["message"]["message_id"] = 43
    await post_updates(repository, pasted, write)

    article = DossierArticle(
        title="Le festival d'Oran",
        paragraphs=[
            HeadedParagraph(
                heading="",
                segments=[
                    SentenceSegment(
                        kind="sentence",
                        text="Le festival s'est ouvert le 3 mai.",
                        evidence_ids=["E1"],
                    )
                ],
            )
        ],
    )
    llm = FakeLLM([article])
    channel = FakeChannel()
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_network)) as pages:
        worker = Worker(repository, build_handlers(channel, pages, llm, DossierRepository(pool)))
        assert await worker.run_once()
        assert await worker.run_once()
        assert not await worker.run_once()

    state = DossierState(count=1, total_chars=len(PASTED))
    line = f"Source 1 ajoutée : texte collé, {len(PASTED)} caractères."
    assert channel.sent[0] == (CHAT, replies.receipt([line], state), "42")
    assert [(chat, reply_to) for chat, _, reply_to in channel.sent[1:]] == [(CHAT, "43")] * 3
    ack, article_text, sources_text = (text for _, text, _ in channel.sent[1:])
    assert ack == replies.write_ack(1)
    assert article_text.startswith("⚠ Matière mince (1 source, ")
    assert article_text.endswith("Le festival d'Oran\n\nLe festival s'est ouvert le 3 mai.")
    assert sources_text.startswith("Sources\n1. Texte collé : Le festival du film d'Oran")
    assert len(llm.prompts) == 1

    cursor = await conn.execute("SELECT status FROM jobs ORDER BY id")
    assert await cursor.fetchall() == [("done",), ("done",)]
    cursor = await conn.execute("SELECT position, kind, message_id FROM dossier_sources")
    assert await cursor.fetchall() == [(1, "pasted_text", "42")]
