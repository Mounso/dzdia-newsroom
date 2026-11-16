"""The `inbound_message` handler: the team group's link path (ADR-0019) and the private chat's
dossier (ADR-0020): routing, receipts, the two messages of `/ecris`, acknowledgments, deadline,
error families, refusals as finished jobs, log line. No real network (httpx.MockTransport), no
real model (FakeLLM) and no database (the in-memory dossier store)."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel

from dzdia_newsroom.channels.base import (
    INBOUND_MESSAGE_KIND,
    ChannelError,
    PermanentChannelError,
)
from dzdia_newsroom.channels.telegram import parse_update
from dzdia_newsroom.db.models import ClaimedJob
from dzdia_newsroom.guardrails.ssrf import BlockedUrlError
from dzdia_newsroom.llm import FakeLLM, Structured
from dzdia_newsroom.tools.article import (
    PROMPT_VERSION,
    ArticleDraft,
    Paragraph,
    SentenceSegment,
)
from dzdia_newsroom.tools.dossier import (
    MAX_SOURCES,
    DossierKey,
    DossierState,
    Source,
)
from dzdia_newsroom.tools.dossier_article import (
    DOSSIER_PROMPT_VERSION,
    DossierArticle,
    HeadedParagraph,
)
from dzdia_newsroom.worker import replies
from dzdia_newsroom.worker.handlers import build_handlers
from dzdia_newsroom.worker.queue import Handler, PermanentJobError, RetryLaterJobError
from tests.dossier_store import FakeStore, store_with, stored
from tests.telegram_updates import make_update, team_update

KEY = DossierKey(channel="telegram", chat_id="111")  # the private chat of user 111


@dataclass
class FakeChannel:
    sent: list[tuple[str, str, str | None]] = field(default_factory=list)
    error: Exception | None = None  # raised on every send
    errors_by_text: dict[str, Exception] = field(default_factory=dict)

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        if self.error is not None:
            raise self.error
        if text in self.errors_by_text:
            raise self.errors_by_text[text]
        self.sent.append((chat_id, text, reply_to))

    @property
    def texts(self) -> list[str]:
        return [text for _, text, _ in self.sent]


def inbound_job(
    payload: object = None,
    *,
    text: str = "Bonjour",
    group: bool = False,
    attempts: int = 1,
    max_attempts: int = 3,
    message_id: str = "42",
) -> ClaimedJob:
    if payload is None:
        update = (
            team_update(user_id=111, text=text) if group else make_update(user_id=111, text=text)
        )
        message = parse_update(update)
        payload = message.model_dump(mode="json")  # type: ignore[union-attr]
        payload["message_id"] = message_id
    return ClaimedJob.model_validate(
        {
            "id": 1,
            "kind": INBOUND_MESSAGE_KIND,
            "payload": payload,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "claim_token": uuid4(),
        }
    )


TITLE = "Oran : une station de dessalement"
PARAGRAPHS = (
    "<p>Le ministre a inauguré mardi une nouvelle station de dessalement à Oran, d'une capacité "
    "de 300 000 mètres cubes par jour, selon un communiqué publié par le ministère.</p>\n"
    "<p>Le projet a coûté 450 millions de dollars et alimentera trois wilayas de l'ouest du pays, "
    "a précisé le directeur de l'Algerian Energy Company lors de la cérémonie.</p>\n"
    "<p>Les travaux avaient commencé il y a quatre ans, et la mise en service progressive doit "
    "s'achever avant l'été prochain, selon le même communiqué du ministère.</p>"
)
LINK = "https://example.org/secret-path?token=abc"
PASTED = (
    "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre régional, devant un public "
    "nombreux venu de toute la région.\n"
    "Douze films sont en compétition cette année, dont quatre premières oeuvres, selon les "
    "organisateurs qui annoncent aussi des ateliers pour les jeunes réalisateurs.\n"
    "La clôture est prévue le 10 mai avec la remise des prix par le jury international."
)


def page(extra: str = "") -> str:
    return (
        f'<html><head><title>{TITLE}</title><meta property="og:title" content="{TITLE}">'
        f"</head><body><article><h1>{TITLE}</h1>\n{PARAGRAPHS}\n{extra}</article></body></html>"
    )


def html(body: str, status: int = 200, content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, text=body)


def article(evidence_id: str = "E1") -> ArticleDraft:
    return ArticleDraft(
        title="Station de dessalement",
        paragraphs=[
            Paragraph(
                segments=[
                    SentenceSegment(
                        kind="sentence",
                        text="Une station a été inaugurée à Oran.",
                        evidence_ids=[evidence_id],
                    )
                ]
            )
        ],
    )


ARTICLE_TEXT = "Station de dessalement\n\nUne station a été inaugurée à Oran."


class SlowLLM:
    """A generator that never answers in time."""

    model = "slow-llm"
    thinking_level = None

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def generate_structured[M: BaseModel](
        self, prompt: str, schema: type[M]
    ) -> Structured[M]:
        self.started.set()
        await asyncio.sleep(30)
        raise AssertionError("the deadline should have expired")


type Respond = Callable[[httpx.Request], httpx.Response]


@dataclass
class Pages:
    """The guarded client of the worker, on a mock transport that records requests."""

    respond: Respond = field(default=lambda request: html(page()))
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)


@pytest.fixture
async def pages() -> AsyncIterator[tuple[Pages, httpx.AsyncClient]]:
    recorder = Pages()
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle)) as client:
        yield recorder, client


def handler_for(
    channel: FakeChannel,
    client: httpx.AsyncClient,
    llm: Any,
    store: Any = None,
    **kwargs: float,
) -> Handler:
    dossiers = FakeStore() if store is None else store
    return build_handlers(channel, client, llm, dossiers, **kwargs)[INBOUND_MESSAGE_KIND]


# --- private routing -------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Bonjour", "/start", "/aide", "a" * 299])
async def test_a_private_message_that_is_no_source_gets_the_help(
    pages: tuple[Pages, httpx.AsyncClient], text: str
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    llm = FakeLLM([])
    await handler_for(channel, client, llm)(inbound_job(text=text))
    assert channel.sent == [("111", replies.help_text(DossierState()), "42")]
    assert recorder.requests == [] and llm.prompts == []


async def test_the_help_shows_the_dossier_and_is_not_activity(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    store = store_with(KEY, stored(1, "a" * 500), stored(2, "b" * 700))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([]), store)(inbound_job(text="Bonjour"))
    assert channel.texts == [replies.help_text(DossierState(count=2, total_chars=1_200))]
    assert store.touches == []


@pytest.mark.parametrize("text", ["Bonjour", PASTED, "www.example.org/article", "/ecris"])
async def test_a_group_message_without_link_gets_no_answer(
    pages: tuple[Pages, httpx.AsyncClient], text: str
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    llm = FakeLLM([])
    store = FakeStore()
    await handler_for(channel, client, llm, store)(inbound_job(text=text, group=True))
    assert channel.sent == []
    assert recorder.requests == [] and llm.prompts == [] and store.dossiers == {}


# --- private sources (ADR-0020, point 4) -----------------------------------------------------


async def test_a_link_joins_the_dossier_with_one_receipt_and_no_acknowledgment(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    store, channel = FakeStore(), FakeChannel()
    llm = FakeLLM([])
    await handler_for(channel, client, llm, store)(inbound_job(text=f"Lis {LINK}."))

    [source] = await store.sources(KEY)
    assert source.url == LINK and source.position == 1
    state = DossierState(count=1, total_chars=len(source.text), urls=frozenset({LINK}))
    line = f"Source 1 ajoutée : example.org, {replies.format_chars(len(source.text))} caractères."
    assert channel.sent == [("111", replies.receipt([line], state), "42")]
    assert [str(request.url) for request in recorder.requests] == [LINK]
    assert llm.prompts == []


async def test_a_pasted_text_joins_the_dossier(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    recorder, client = pages
    store, channel = FakeStore(), FakeChannel()
    await handler_for(channel, client, FakeLLM([]), store)(inbound_job(text=f"  {PASTED}\n"))
    [source] = await store.sources(KEY)
    assert source.text == PASTED and source.url is None
    assert channel.texts == [
        replies.receipt(
            [f"Source 1 ajoutée : texte collé, {len(PASTED)} caractères."],
            DossierState(count=1, total_chars=len(PASTED)),
        )
    ]
    assert recorder.requests == []


async def test_a_pasted_text_with_a_link_is_stored_whole_and_fetches_nothing(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    store, channel = FakeStore(), FakeChannel()
    text = f"{PASTED}\n{LINK}"
    await handler_for(channel, client, FakeLLM([]), store)(inbound_job(text=text))
    [source] = await store.sources(KEY)
    assert source.text == text and source.url is None
    assert channel.texts == [
        replies.receipt(
            [f"Source 1 ajoutée : texte collé, {len(text)} caractères."],
            DossierState(count=1, total_chars=len(text)),
        )
    ]
    assert recorder.requests == []


def by_host(request: httpx.Request) -> httpx.Response:
    if request.url.host == "gone.example":
        return html("", 404)
    if request.url.host == "closed.example":
        return html("", 403)
    return html(page())


async def test_several_links_with_mixed_outcomes_give_one_receipt_and_the_job_is_done(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    recorder.respond = by_host
    already = "https://known.example/a"
    store = store_with(KEY, stored(1, "k" * 500, url=already))
    channel = FakeChannel()
    text = (
        f"{LINK} https://gone.example/x https://www.youtube.com/watch?v=1 {already} "
        f"https://closed.example/y {LINK} https://example.org:8080/z"
    )
    await handler_for(channel, client, FakeLLM([]), store)(inbound_job(text=text))

    sources = await store.sources(KEY)
    assert [source.url for source in sources] == [already, LINK]
    added_chars = len(sources[1].text)
    [receipt] = channel.texts
    assert receipt.splitlines() == [
        f"Source 2 ajoutée : example.org, {replies.format_chars(added_chars)} caractères.",
        "gone.example : je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il "
        "s'ouvre dans un navigateur, puis renvoyez-le.",
        "www.youtube.com : je ne lis pas encore les vidéos ni les publications des réseaux "
        "sociaux. Collez-moi le texte ou la légende (au moins 300 caractères) et je l'ajoute au "
        "dossier.",
        "known.example : ce lien est déjà dans le dossier.",
        "closed.example : le site ne m'a pas laissé lire cette page. Collez-moi le texte de "
        "l'article dans un message et je l'ajoute au dossier.",
        "example.org : je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il "
        "s'ouvre dans un navigateur, puis renvoyez-le.",
        "",
        replies.state_line(DossierState(count=2, total_chars=500 + added_chars)),
        replies.NEXT_STEPS,
    ]
    # The duplicate of LINK in the message is read once; refused links are never fetched.
    assert [request.url.host for request in recorder.requests] == [
        "example.org",
        "gone.example",
        "closed.example",
    ]


async def test_a_full_dossier_refuses_a_link_without_a_fetch(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    store = store_with(KEY, *(stored(n, "a" * 500) for n in range(1, MAX_SOURCES + 1)))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([]), store)(inbound_job(text=LINK))
    assert channel.texts[0].startswith("example.org : non ajouté, le dossier est plein.\n\n")
    assert recorder.requests == []


async def test_a_full_dossier_refuses_a_text(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    _, client = pages
    store = store_with(KEY, *(stored(n, "a" * 500) for n in range(1, MAX_SOURCES + 1)))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([]), store)(inbound_job(text=PASTED))
    assert channel.texts[0].startswith("Texte non ajouté : le dossier est plein.\n\n")


async def test_a_replayed_source_job_adds_nothing_and_sends_its_receipt_again(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    store, channel = FakeStore(), FakeChannel()
    handler = handler_for(channel, client, FakeLLM([]), store)
    await handler(inbound_job(text=LINK))
    await handler(inbound_job(text=LINK, attempts=2))
    assert len(await store.sources(KEY)) == 1
    assert channel.texts[0] == channel.texts[1]
    assert len(recorder.requests) == 1


async def test_the_deadline_covers_every_fetch_of_a_source_job(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages

    class SlowStore(FakeStore):
        async def state(self, key: DossierKey) -> DossierState:
            await asyncio.sleep(30)
            raise AssertionError("the deadline should have expired")

    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([]), SlowStore(), deadline_s=0.05)
    with pytest.raises(PermanentJobError, match=r"TimeoutError: deadline of 0\.05 s expired"):
        await handler(inbound_job(text=LINK))
    assert channel.texts == [replies.SOURCE_INSIDE_ERROR]


class BrokenStore(FakeStore):
    async def add_source(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("database down")


@pytest.mark.parametrize(("attempts", "sent"), [(2, []), (3, [replies.SOURCE_INSIDE_ERROR])])
async def test_an_unknown_error_of_a_source_job_is_left_to_the_queue(
    pages: tuple[Pages, httpx.AsyncClient], attempts: int, sent: list[str]
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([]), BrokenStore())
    with pytest.raises(RuntimeError, match="database down"):
        await handler(inbound_job(text=PASTED, attempts=attempts, max_attempts=3))
    assert channel.texts == sent


# --- /ecris (ADR-0020, point 12) -------------------------------------------------------------

SOURCE_ONE = "Le festival du film d'Oran s'est ouvert le 3 mai.\n" + "a" * 3_000
SOURCE_TWO = "La clôture est prévue le 10 mai.\n" + "b" * 1_000
# E1, E2: source 1; E3, E4: source 2.


def dossier_article(*, heading: str = "La clôture") -> DossierArticle:
    def sentence(text: str, *ids: str) -> SentenceSegment:
        return SentenceSegment(kind="sentence", text=text, evidence_ids=list(ids))

    return DossierArticle(
        title="Le festival d'Oran",
        paragraphs=[
            HeadedParagraph(heading="", segments=[sentence("Le festival a ouvert.", "E1")]),
            HeadedParagraph(heading=heading, segments=[sentence("La clôture.", "E3", "E1")]),
        ],
    )


def written_store() -> FakeStore:
    return store_with(
        KEY,
        stored(1, SOURCE_ONE, url="https://example.org/ouverture"),
        stored(2, SOURCE_TWO),
    )


async def test_ecris_acknowledges_then_sends_the_article_and_its_sources(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    store, channel = written_store(), FakeChannel()
    llm = FakeLLM([dossier_article()])
    await handler_for(channel, client, llm, store)(inbound_job(text="/ecris", message_id="77"))

    assert [(chat, reply_to) for chat, _, reply_to in channel.sent] == [("111", "77")] * 3
    ack, article_text, sources_text = channel.texts
    assert ack == "J'écris l'article à partir de 2 sources."
    assert article_text == (
        "Le festival d'Oran\n\nLe festival a ouvert.\n\nLa clôture\n\nLa clôture."
    )
    assert sources_text == (
        "Sources\n1. example.org\nhttps://example.org/ouverture\n"
        "2. Texte collé : La clôture est prévue le 10 mai.…\n\n"
        "Paragraphes\n1. Le festival a ouvert. → 1\n2. La clôture. → 1, 2"
    )
    assert "dans la limite de" in llm.prompts[0] and recorder.requests == []
    assert store.touches == [KEY]


async def test_no_acknowledgment_of_ecris_after_the_first_attempt(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([dossier_article()]), written_store())
    await handler(inbound_job(text="écris", attempts=2))
    assert len(channel.sent) == 2 and channel.texts[0].startswith("Le festival d'Oran")


async def test_a_thin_dossier_gets_the_warning_and_plain_paragraphs(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    store = store_with(KEY, stored(1, SOURCE_TWO))
    article = DossierArticle(
        title="Clôture",
        paragraphs=[
            HeadedParagraph(
                heading="",
                segments=[SentenceSegment(kind="sentence", text="Fin.", evidence_ids=["E1"])],
            )
        ],
    )
    llm = FakeLLM([article])
    channel = FakeChannel()
    await handler_for(channel, client, llm, store)(inbound_job(text="/ecris"))
    warning = replies.thin_warning(1, len(SOURCE_TWO))
    assert channel.texts[1] == f"{warning}\n\nClôture\n\nFin."
    assert "- Structure : des paragraphes simples, sans intertitre." in llm.prompts[0]


async def test_ecris_on_an_empty_dossier_calls_no_model(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    llm = FakeLLM([])
    await handler_for(channel, client, llm)(inbound_job(text="/ecris"))
    assert channel.texts == [replies.EMPTY_DOSSIER]
    assert llm.prompts == []


async def test_a_dossier_emptied_after_the_first_look_is_an_empty_dossier(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages

    class VanishingStore(FakeStore):
        async def sources(self, key: DossierKey) -> list[Source]:
            return []

    store = VanishingStore()
    store.dossiers = written_store().dossiers
    channel = FakeChannel()
    llm = FakeLLM([])
    await handler_for(channel, client, llm, store)(inbound_job(text="/ecris"))
    assert channel.texts == [replies.write_ack(2), replies.EMPTY_DOSSIER]
    assert llm.prompts == []


async def test_a_failed_generation_sends_the_inside_text_and_is_permanent(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    bad = dossier_article(heading="« Partie »")
    channel = FakeChannel()
    with pytest.raises(PermanentJobError, match="GenerationFailedError"):
        await handler_for(channel, client, FakeLLM([bad, bad]), written_store())(
            inbound_job(text="/ecris")
        )
    assert channel.texts == [replies.write_ack(2), replies.WRITE_INSIDE_ERROR]


async def test_the_deadline_of_ecris_sends_the_inside_text_and_is_permanent(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, SlowLLM(), written_store(), deadline_s=0.05)
    with pytest.raises(PermanentJobError, match="TimeoutError"):
        await handler(inbound_job(text="/ecris"))
    assert channel.texts == [replies.write_ack(2), replies.WRITE_INSIDE_ERROR]


@pytest.mark.parametrize(("attempts", "sent"), [(2, []), (3, [replies.WRITE_INSIDE_ERROR])])
async def test_an_unknown_error_of_ecris_is_left_to_the_queue(
    pages: tuple[Pages, httpx.AsyncClient], attempts: int, sent: list[str]
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([RuntimeError("down")]), written_store())
    with pytest.raises(RuntimeError, match="down"):
        await handler(inbound_job(text="/ecris", attempts=attempts, max_attempts=3))
    assert channel.texts == sent


# --- /nouveau --------------------------------------------------------------------------------


async def test_nouveau_empties_the_dossier(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    _, client = pages
    store, channel = written_store(), FakeChannel()
    handler = handler_for(channel, client, FakeLLM([]), store)
    await handler(inbound_job(text="/nouveau"))
    await handler(inbound_job(text="nouveau"))
    assert channel.texts == ["Dossier vidé : 2 sources retirées.", "Le dossier est déjà vide."]
    assert await store.sources(KEY) == []


# --- team group: success ---------------------------------------------------------------------


async def test_a_group_link_is_acknowledged_then_answered_with_a_draft(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    store = FakeStore()
    await handler_for(channel, client, FakeLLM([article()]), store)(
        inbound_job(text=f"Lis {LINK}.", group=True)
    )
    assert channel.sent == [
        ("-100123", replies.ACK_LINK, "42"),
        ("-100123", f"{ARTICLE_TEXT}\n\nSource : {LINK}", "42"),
    ]
    assert [str(request.url) for request in recorder.requests] == [LINK]
    assert store.dossiers == {}


async def test_no_acknowledgment_after_the_first_attempt(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(
        inbound_job(text=LINK, group=True, attempts=2)
    )
    assert channel.texts == [f"{ARTICLE_TEXT}\n\nSource : {LINK}"]


async def test_several_links_are_acknowledged_as_such_and_only_the_first_is_fetched(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    text = f"{LINK} et https://other.example/b"
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=text, group=True))
    assert channel.texts[0] == replies.ACK_SEVERAL_LINKS
    assert [str(request.url) for request in recorder.requests] == [LINK]
    assert channel.texts[1].endswith(f"Source : {LINK}")


async def test_a_paywalled_page_gets_its_warning(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    recorder, client = pages
    recorder.respond = lambda request: html(page("<p>Ce contenu est réservé aux abonnés.</p>"))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=LINK, group=True))
    assert channel.texts[1].startswith(f"{replies.PAYWALL_WARNING}\n\n{ARTICLE_TEXT}")


async def test_a_long_page_gets_the_truncation_warning(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    long_paragraphs = "\n".join(
        "<p>"
        + f"Le chantier {n} avance selon le calendrier du ministère, bilan numéro {n}. " * 6
        + "</p>"
        for n in range(80)
    )
    recorder.respond = lambda request: html(page(long_paragraphs))
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([article()]))(inbound_job(text=LINK, group=True))
    assert channel.texts[1].startswith(f"{replies.TRUNCATED_WARNING}\n\n{ARTICLE_TEXT}")


# --- team group: refusals are finished jobs (ADR-0020, point 14) -----------------------------


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://www.instagram.com/reel/abc", replies.UNSUPPORTED_LINK_ERROR),
        ("https://youtu.be/xyz", replies.UNSUPPORTED_LINK_ERROR),
        ("https://example.org:8080/a", replies.LINK_ERROR),
        ("https://user:pass@example.org/a", replies.LINK_ERROR),
    ],
)
async def test_a_link_refused_before_the_network_gets_its_text_and_the_job_ends(
    pages: tuple[Pages, httpx.AsyncClient], link: str, expected: str
) -> None:
    recorder, client = pages
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([]))(inbound_job(text=link, group=True))
    assert channel.texts == [expected]
    assert recorder.requests == []


def _blocked(request: httpx.Request) -> httpx.Response:
    raise BlockedUrlError("host 'example.org' resolves to an address that is not allowed")


REFUSAL_CASES: dict[str, tuple[Respond, str]] = {
    "not found": (lambda r: html("", 404), replies.LINK_ERROR),
    "gone": (lambda r: html("", 410), replies.LINK_ERROR),
    "blocked": (_blocked, replies.LINK_ERROR),
    "refused": (lambda r: html("", 403), replies.SITE_ERROR),
    "rate limited": (lambda r: html("", 429), replies.SITE_ERROR),
    "not html": (lambda r: html("%PDF", content_type="application/pdf"), replies.SITE_ERROR),
    "no readable text": (
        lambda r: html("<html><body><p>Trop court.</p></body></html>"),
        replies.SITE_ERROR,
    ),
    "redirect to a social site": (
        lambda r: httpx.Response(302, headers={"location": "https://www.tiktok.com/@a/video/1"}),
        replies.UNSUPPORTED_LINK_ERROR,
    ),
}


@pytest.mark.parametrize("case", list(REFUSAL_CASES))
async def test_each_refusal_family_sends_its_text_and_the_job_ends(
    pages: tuple[Pages, httpx.AsyncClient], case: str
) -> None:
    respond, expected = REFUSAL_CASES[case]
    recorder, client = pages
    recorder.respond = respond
    channel = FakeChannel()
    await handler_for(channel, client, FakeLLM([]))(inbound_job(text=LINK, group=True))
    assert channel.texts == [replies.ACK_LINK, expected]


async def test_a_failed_generation_in_the_group_is_permanent(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    with pytest.raises(PermanentJobError) as raised:
        await handler_for(channel, client, FakeLLM([article("E99"), article("E99")]))(
            inbound_job(text=LINK, group=True)
        )
    assert channel.texts == [replies.ACK_LINK, replies.INSIDE_ERROR]
    message = str(raised.value)
    assert message.startswith("GenerationFailedError: ")
    assert "secret-path" not in message and "token" not in message


# --- team group: deadline and other errors ---------------------------------------------------


async def test_the_deadline_sends_the_inside_text_and_is_permanent(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, SlowLLM(), deadline_s=0.05)
    with pytest.raises(PermanentJobError, match="TimeoutError"):
        await handler(inbound_job(text=LINK, group=True))
    assert channel.texts == [replies.ACK_LINK, replies.INSIDE_ERROR]


async def test_another_timeout_is_not_the_deadline(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([TimeoutError("read timeout")]))
    with pytest.raises(TimeoutError, match="read timeout"):
        await handler(inbound_job(text=LINK, group=True))
    assert channel.texts == [replies.ACK_LINK]


async def test_the_deadline_does_not_cover_the_sends(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages

    @dataclass
    class SlowChannel(FakeChannel):
        async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
            await asyncio.sleep(0.6)  # each send alone outlasts the deadline
            await super().send_text(chat_id, text, reply_to=reply_to)

    channel = SlowChannel()
    handler = handler_for(channel, client, FakeLLM([article()]), deadline_s=0.5)
    await handler(inbound_job(text=LINK, group=True))
    assert channel.texts == [replies.ACK_LINK, f"{ARTICLE_TEXT}\n\nSource : {LINK}"]


async def test_a_cancelled_job_is_not_caught(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    _, client = pages
    channel = FakeChannel()
    llm = SlowLLM()
    task = asyncio.create_task(
        handler_for(channel, client, llm)(inbound_job(text=LINK, group=True, attempts=3))
    )
    await llm.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert channel.sent == []


async def test_an_unknown_error_is_left_to_the_queue_with_no_message(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(RuntimeError, match="provider down"):
        await handler(inbound_job(text=LINK, group=True, attempts=2, max_attempts=3))
    assert channel.sent == []


async def test_an_unknown_error_on_the_last_attempt_sends_the_inside_text_first(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel()
    handler = handler_for(channel, client, FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(RuntimeError, match="provider down"):
        await handler(inbound_job(text=LINK, group=True, attempts=3, max_attempts=3))
    assert channel.texts == [replies.INSIDE_ERROR]


async def test_a_failed_inside_text_does_not_replace_the_original_error(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    channel = FakeChannel(
        errors_by_text={replies.INSIDE_ERROR: PermanentChannelError("sendMessage: rejected")}
    )
    handler = handler_for(channel, client, FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(RuntimeError, match="provider down"):
        await handler(inbound_job(text=LINK, group=True, attempts=3, max_attempts=3))
    assert any("inside" in record.getMessage() for record in caplog.records)


# --- channel errors --------------------------------------------------------------------------


async def test_rejected_send_is_permanent(pages: tuple[Pages, httpx.AsyncClient]) -> None:
    _, client = pages
    channel = FakeChannel(error=PermanentChannelError("sendMessage: rejected (403)"))
    with pytest.raises(PermanentJobError, match="rejected"):
        await handler_for(channel, client, FakeLLM([]))(inbound_job())


async def test_transient_send_failure_is_left_to_the_queue_retries(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel(error=ChannelError("sendMessage: server error 502"))
    with pytest.raises(ChannelError) as raised:
        await handler_for(channel, client, FakeLLM([]))(inbound_job())
    assert not isinstance(raised.value, PermanentJobError)


async def test_rate_limit_is_passed_to_the_queue_as_a_minimum_delay(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel(error=ChannelError("sendMessage: rate limited", retry_after_s=120))
    with pytest.raises(RetryLaterJobError) as raised:
        await handler_for(channel, client, FakeLLM([]))(inbound_job())
    assert raised.value.delay_s == 120


async def test_a_failed_acknowledgment_is_translated_too(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    recorder, client = pages
    channel = FakeChannel(errors_by_text={replies.ACK_LINK: ChannelError("x", retry_after_s=5)})
    with pytest.raises(RetryLaterJobError):
        await handler_for(channel, client, FakeLLM([]))(inbound_job(text=LINK, group=True))
    assert recorder.requests == []


async def test_a_failed_error_text_is_translated(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    channel = FakeChannel(
        errors_by_text={replies.UNSUPPORTED_LINK_ERROR: PermanentChannelError("rejected (403)")}
    )
    with pytest.raises(PermanentJobError, match="rejected"):
        await handler_for(channel, client, FakeLLM([]))(
            inbound_job(text="https://x.com/a/1", group=True)
        )


async def test_invalid_payload_is_permanent_and_does_not_leak_values(
    pages: tuple[Pages, httpx.AsyncClient],
) -> None:
    _, client = pages
    handler = handler_for(FakeChannel(), client, FakeLLM([]))
    with pytest.raises(PermanentJobError) as raised:
        await handler(inbound_job({"text": "texte confidentiel"}))
    assert "confidentiel" not in str(raised.value)


# --- log line (ADR-0019, point 11; ADR-0020, point 15) ---------------------------------------


def _job_record(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    [record] = [r for r in caplog.records if r.name == "dzdia_newsroom.worker.handlers"]
    return record.__dict__


async def test_one_log_line_for_a_group_link_without_text_path_or_article(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    recorder, client = pages
    recorder.respond = lambda request: html(page("<p>Ce contenu est réservé aux abonnés.</p>"))
    caplog.set_level(logging.INFO)
    await handler_for(FakeChannel(), client, FakeLLM([article()]))(
        inbound_job(text=f"Vu {LINK}", group=True)
    )
    fields = _job_record(caplog)
    assert fields["levelno"] == logging.INFO
    assert (fields["route"], fields["outcome"], fields["host"]) == ("link", "ok", "example.org")
    assert fields["source_chars"] > 300 and fields["evidence_count"] >= 3
    assert (fields["model_calls"], fields["removed_quotes"]) == (1, 0)
    assert fields["warnings"] == ["paywall"]
    assert (fields["model"], fields["prompt_version"]) == ("fake-llm", PROMPT_VERSION)
    assert isinstance(fields["duration_ms"], int)
    logged = f"{fields}"
    assert "secret-path" not in logged and "token=" not in logged
    assert "Vu " not in logged and "inaugurée" not in logged


async def test_a_group_refusal_is_logged_at_warning_with_its_type(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    recorder, client = pages
    recorder.respond = lambda request: html("", 404)
    caplog.set_level(logging.INFO)
    await handler_for(FakeChannel(), client, FakeLLM([]))(inbound_job(text=LINK, group=True))
    fields = _job_record(caplog)
    assert (fields["levelno"], fields["outcome"]) == (logging.WARNING, "FetchError")


async def test_the_log_line_names_the_generation_failure(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    with pytest.raises(PermanentJobError):
        await handler_for(FakeChannel(), client, FakeLLM([article("E9"), article("E9")]))(
            inbound_job(text=LINK, group=True)
        )
    fields = _job_record(caplog)
    assert (fields["outcome"], fields["model_calls"]) == ("GenerationFailedError", 2)


async def test_the_log_line_of_a_source_job(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    recorder, client = pages
    recorder.respond = by_host
    caplog.set_level(logging.INFO)
    store = store_with(KEY, stored(1, "k" * 500, url="https://known.example/a"))
    text = f"{LINK} https://gone.example/x?id=7 https://known.example/a"
    await handler_for(FakeChannel(), client, FakeLLM([]), store)(inbound_job(text=text))
    fields = _job_record(caplog)
    assert fields["levelno"] == logging.WARNING
    assert (fields["route"], fields["outcome"], fields["prompt_version"]) == (
        "source",
        "FetchError",
        None,
    )
    assert fields["links"] == [
        {"host": "example.org", "outcome": "ok"},
        {"host": "gone.example", "outcome": "FetchError"},
        {"host": "known.example", "outcome": "already_there"},
    ]
    [_, added] = await store.sources(KEY)
    assert (fields["dossier_sources"], fields["dossier_chars"]) == (2, 500 + len(added.text))
    logged = f"{fields}"
    assert "secret-path" not in logged and "id=7" not in logged
    assert "known.example/a" not in logged


async def test_a_source_job_where_everything_is_added_is_ok(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    await handler_for(FakeChannel(), client, FakeLLM([]))(inbound_job(text=PASTED))
    fields = _job_record(caplog)
    assert (fields["levelno"], fields["outcome"], fields["links"]) == (logging.INFO, "ok", [])
    assert (fields["dossier_sources"], fields["dossier_chars"]) == (1, len(PASTED))
    assert "festival" not in f"{fields}"


async def test_a_refused_pasted_text_names_the_refusal(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    store = store_with(KEY, *(stored(n, "a" * 500) for n in range(1, MAX_SOURCES + 1)))
    await handler_for(FakeChannel(), client, FakeLLM([]), store)(inbound_job(text=PASTED))
    assert _job_record(caplog)["outcome"] == "full"


async def test_the_log_line_of_ecris(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    store = written_store()
    await handler_for(FakeChannel(), client, FakeLLM([dossier_article()]), store)(
        inbound_job(text="/ecris")
    )
    fields = _job_record(caplog)
    assert (fields["route"], fields["outcome"]) == ("write", "ok")
    assert fields["prompt_version"] == DOSSIER_PROMPT_VERSION
    assert (fields["dossier_sources"], fields["dossier_chars"]) == (
        2,
        len(SOURCE_ONE) + len(SOURCE_TWO),
    )
    assert fields["budget_chars"] > 2_000 and fields["max_words"] >= 100
    assert fields["article_chars"] == len(
        "Le festival d'Oran\n\nLe festival a ouvert.\n\nLa clôture\n\nLa clôture."
    )
    assert (fields["paragraphs"], fields["headings"]) == (2, 1)
    assert (fields["sources_cited"], fields["sources_unused"]) == ([1, 2], [])
    assert (fields["model_calls"], fields["removed_quotes"]) == (1, 0)
    logged = f"{fields}"
    assert "Le festival a ouvert" not in logged and "La clôture" not in logged


async def test_an_empty_dossier_is_logged_at_warning(
    pages: tuple[Pages, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    await handler_for(FakeChannel(), client, FakeLLM([]))(inbound_job(text="/ecris"))
    fields = _job_record(caplog)
    assert (fields["levelno"], fields["outcome"]) == (logging.WARNING, "EmptyDossierError")


@pytest.mark.parametrize(
    ("text", "group", "route"),
    [
        ("Bonjour", False, "help"),
        ("Bonjour", True, "silent"),
        (PASTED, False, "source"),
        ("/nouveau", False, "new"),
    ],
)
async def test_the_log_line_names_the_route(
    pages: tuple[Pages, httpx.AsyncClient],
    caplog: pytest.LogCaptureFixture,
    text: str,
    group: bool,
    route: str,
) -> None:
    _, client = pages
    caplog.set_level(logging.INFO)
    await handler_for(FakeChannel(), client, FakeLLM([]))(inbound_job(text=text, group=group))
    fields = _job_record(caplog)
    assert (fields["route"], fields["outcome"], fields["host"]) == (route, "ok", None)
    assert fields["prompt_version"] is None
