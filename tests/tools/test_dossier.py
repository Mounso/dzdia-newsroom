"""Specification tests of the source dossier (ADR-0020, points 4, 5, 7, 9, 11 and 16): the caps,
the budget, the decision function, and the add, clear and write operations on an in-memory store
that follows the contract of `DossierStore`. No real network (httpx.MockTransport) and no real
model (FakeLLM); the Postgres repository has its own tests under `tests/db/`."""

from dataclasses import dataclass, field, replace

import httpx
import pytest

from dzdia_newsroom.llm import FakeLLM
from dzdia_newsroom.tools.article import GenerationFailedError, SentenceSegment
from dzdia_newsroom.tools.dossier import (
    BUDGET_CEILING_CHARS,
    DOSSIER_TTL,
    MAX_DOSSIER_CHARS,
    MAX_SOURCE_CHARS,
    MAX_SOURCES,
    Added,
    Candidate,
    DossierKey,
    DossierState,
    EmptyDossierError,
    Refusal,
    RefusalReason,
    Refused,
    SourceKind,
    add_link,
    add_text,
    clear,
    decide,
    length_budget,
    read_state,
    write_article,
)
from dzdia_newsroom.tools.dossier_article import (
    DossierArticle,
    HeadedParagraph,
    build_dossier_prompt,
)
from dzdia_newsroom.tools.evidence import MAX_INPUT_CHARS, build_dossier_evidence, cap_text
from dzdia_newsroom.tools.fetch_page import FetchError, InvalidLinkError, UnsupportedLinkError
from dzdia_newsroom.tools.page_text import MIN_SOURCE_CHARS, NoReadableTextError
from tests.dossier_store import FakeStore, store_with, stored

KEY = DossierKey(channel="telegram", chat_id="42")
OTHER_KEY = DossierKey(channel="telegram", chat_id="43")
URL = "https://example.org/article"


# --- budget (ADR-0020, point 11) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("lengths", "chars", "max_words", "thin"),
    [
        ([1_800], 1_440, 250, True),
        ([4_000], 3_200, 550, False),
        ([3_000] * 3, 4_800, 800, False),
        ([5_000] * 4, 6_000, 1_000, False),
    ],
)
def test_the_budget_reproduces_the_adr_table(
    lengths: list[int], chars: int, max_words: int, thin: bool
) -> None:
    budget = length_budget(lengths)
    assert (budget.chars, budget.max_words, budget.thin) == (chars, max_words, thin)
    assert budget.with_headings is not thin


def test_the_longest_source_counts_most_whatever_the_order() -> None:
    # 0.8 x 3,000 + 0.4 x (1,000 + 500) = 3,000
    assert length_budget([1_000, 3_000, 500]).chars == 3_000


def test_the_budget_has_a_ceiling() -> None:
    assert length_budget([30_000, 30_000]).chars == BUDGET_CEILING_CHARS


def test_the_word_maximum_has_a_floor() -> None:
    budget = length_budget([300])
    assert (budget.chars, budget.max_words, budget.thin) == (240, 100, True)


def test_the_thin_threshold_is_strict() -> None:
    assert length_budget([2_500]).thin is False  # 2,000 exactly
    assert length_budget([2_499]).thin is True


def test_the_word_maximum_rounds_half_up_to_fifty() -> None:
    assert length_budget([1_640]).max_words == 200  # 1,312 chars: about 219 words
    assert length_budget([1_687]).max_words == 250  # 1,350 chars: 225 words, half up


# --- decision (ADR-0020, points 4 and 5) -----------------------------------------------------


def candidate(
    text: str = "x" * 1_000,
    *,
    url: str | None = URL,
    truncated: bool = False,
    paywall: bool = False,
) -> Candidate:
    return Candidate(
        kind=SourceKind.LINK if url is not None else SourceKind.PASTED_TEXT,
        url=url,
        text=text,
        source_chars=len(text),
        truncated=truncated,
        paywall_suspected=paywall,
    )


def test_an_empty_dossier_accepts_a_source_as_it_is() -> None:
    given = candidate(paywall=True)
    assert decide(DossierState(), given) == given


def test_a_link_already_in_the_dossier_is_refused() -> None:
    state = DossierState(count=1, total_chars=1_000, urls=frozenset({URL}))
    assert decide(state, candidate()) == Refusal(RefusalReason.ALREADY_THERE)


def test_links_are_compared_as_exact_strings() -> None:
    state = DossierState(count=1, total_chars=1_000, urls=frozenset({URL}))
    for other in (URL + "/", URL.upper(), URL + "?a=1"):
        assert isinstance(decide(state, candidate(url=other)), Candidate)


def test_a_pasted_text_is_never_already_there() -> None:
    state = DossierState(count=1, total_chars=1_000, urls=frozenset({URL}))
    assert isinstance(decide(state, candidate(url=None)), Candidate)


def test_already_there_wins_over_full() -> None:
    state = DossierState(count=MAX_SOURCES, total_chars=1_000, urls=frozenset({URL}))
    assert decide(state, candidate()) == Refusal(RefusalReason.ALREADY_THERE)


def test_a_dossier_at_the_source_cap_is_full() -> None:
    state = DossierState(count=MAX_SOURCES, total_chars=1_000)
    assert decide(state, candidate(url=None)) == Refusal(RefusalReason.FULL)
    below = DossierState(count=MAX_SOURCES - 1, total_chars=1_000)
    assert isinstance(decide(below, candidate(url=None)), Candidate)


def test_a_dossier_with_too_little_room_is_full() -> None:
    almost = DossierState(count=2, total_chars=MAX_DOSSIER_CHARS - MIN_SOURCE_CHARS + 1)
    assert decide(almost, candidate()) == Refusal(RefusalReason.FULL)


def test_a_source_is_cut_to_the_room_left() -> None:
    text = "\n".join(f"Paragraphe {n} " + "x" * 90 for n in range(20))  # about 2,000 chars
    room = MIN_SOURCE_CHARS + 50
    state = DossierState(count=2, total_chars=MAX_DOSSIER_CHARS - room)
    accepted = decide(state, candidate(text))
    assert isinstance(accepted, Candidate)
    assert (accepted.text, accepted.truncated) == (cap_text(text, room)[0], True)
    assert len(accepted.text) <= room
    assert accepted.source_chars == len(text)  # the length before every cap


def test_a_source_cut_under_the_minimum_by_the_room_left_is_refused_as_full() -> None:
    text = "x" * 100 + "\n" + "y" * 500  # the cut to 400 falls on the line break: 100 left
    state = DossierState(count=2, total_chars=MAX_DOSSIER_CHARS - 400)
    assert decide(state, candidate(text)) == Refusal(RefusalReason.FULL)


def test_a_source_cut_to_exactly_the_minimum_is_accepted() -> None:
    text = "x" * MIN_SOURCE_CHARS + "\n" + "y" * 500
    state = DossierState(count=2, total_chars=MAX_DOSSIER_CHARS - 400)
    accepted = decide(state, candidate(text))
    assert isinstance(accepted, Candidate)
    assert (accepted.text, accepted.truncated) == ("x" * MIN_SOURCE_CHARS, True)


def test_a_source_that_fits_exactly_is_not_cut() -> None:
    text = "y" * MIN_SOURCE_CHARS
    state = DossierState(count=1, total_chars=MAX_DOSSIER_CHARS - MIN_SOURCE_CHARS)
    assert decide(state, candidate(text)) == candidate(text)


def test_a_source_cut_upstream_stays_flagged() -> None:
    given = candidate(truncated=True)
    assert decide(DossierState(), given) == given


# --- add_link --------------------------------------------------------------------------------

TITLE = "Oran : une station de dessalement"
PARAGRAPHS = (
    "<p>Le ministre a inauguré mardi une nouvelle station de dessalement à Oran, d'une capacité "
    "de 300 000 mètres cubes par jour, selon un communiqué publié par le ministère.</p>\n"
    "<p>Le projet a coûté 450 millions de dollars et alimentera trois wilayas de l'ouest du pays, "
    "a précisé le directeur de l'Algerian Energy Company lors de la cérémonie.</p>\n"
    "<p>Les travaux avaient commencé il y a quatre ans, et la mise en service progressive doit "
    "s'achever avant l'été prochain, selon le même communiqué du ministère.</p>"
)


def page(extra: str = "", body: str = PARAGRAPHS) -> str:
    return (
        f'<html><head><title>{TITLE}</title><meta property="og:title" content="{TITLE}">'
        f"</head><body><article><h1>{TITLE}</h1>\n{body}\n{extra}</article></body></html>"
    )


@dataclass
class Site:
    """A mock transport that serves one HTML page and records every request."""

    html: str = field(default_factory=page)
    status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            self.status, headers={"content-type": "text/html; charset=utf-8"}, text=self.html
        )


async def link(
    store: FakeStore,
    site: Site,
    url: str = URL,
    *,
    message_id: str = "100",
    link_index: int = 0,
) -> Added | Refused:
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
        return await add_link(store, client, KEY, url, message_id=message_id, link_index=link_index)


async def test_a_link_is_fetched_extracted_and_stored() -> None:
    store, site = FakeStore(), Site(page("<p>Ce contenu est réservé aux abonnés.</p>"))
    outcome = await link(store, site)

    [source] = await store.sources(KEY)
    assert source.kind is SourceKind.LINK and source.url == URL and source.position == 1
    assert "450 millions de dollars" in source.text
    assert source.source_chars == len(source.text)
    assert (source.truncated, source.paywall_suspected) == (False, True)
    assert outcome == Added(
        position=1,
        kind=SourceKind.LINK,
        host="example.org",
        stored_chars=len(source.text),
        truncated=False,
        paywall_suspected=True,
        source_count=1,
        total_chars=len(source.text),
    )
    assert len(site.requests) == 1


async def test_links_take_the_next_positions() -> None:
    store, site = FakeStore(), Site()
    first = await link(store, site, URL + "?a", message_id="1")
    second = await link(store, site, URL + "?b", message_id="1", link_index=1)
    assert isinstance(first, Added) and isinstance(second, Added)
    assert (first.position, second.position) == (1, 2)
    assert (second.source_count, second.total_chars) == (2, 2 * first.stored_chars)


async def test_the_link_is_stored_as_sent() -> None:
    store = FakeStore()
    sent = "HTTPS://Example.org/Article?utm=1#frag"
    await link(store, Site(), sent)
    [source] = await store.sources(KEY)
    assert source.url == sent


async def test_a_long_page_is_capped_per_source() -> None:
    body = "\n".join(
        "<p>"
        + f"Le chantier {n} avance selon le calendrier du ministère, bilan numéro {n}. " * 6
        + "</p>"
        for n in range(80)
    )
    store = FakeStore()
    outcome = await link(store, Site(page(body=body)))
    [source] = await store.sources(KEY)
    assert isinstance(outcome, Added) and outcome.truncated is True
    assert source.source_chars > MAX_SOURCE_CHARS == MAX_INPUT_CHARS
    assert len(source.text) <= MAX_SOURCE_CHARS and source.truncated


async def test_a_page_is_cut_to_the_room_left_in_the_dossier() -> None:
    store = store_with(KEY, stored(1, "a" * 29_600), stored(2, "b" * 30_000))  # 400 left
    outcome = await link(store, Site())
    assert isinstance(outcome, Added)
    assert outcome.truncated is True and outcome.stored_chars <= 400
    assert outcome.total_chars <= MAX_DOSSIER_CHARS


async def test_a_link_already_in_the_dossier_is_refused_without_a_fetch() -> None:
    store, site = store_with(KEY, stored(1, "a" * 500, url=URL)), Site()
    assert await link(store, site) == Refused(RefusalReason.ALREADY_THERE)
    assert site.requests == []
    assert len(await store.sources(KEY)) == 1


async def test_a_full_dossier_refuses_a_link_without_a_fetch() -> None:
    store = store_with(KEY, *(stored(n, "a" * 500) for n in range(1, MAX_SOURCES + 1)))
    site = Site()
    assert await link(store, site) == Refused(RefusalReason.FULL)
    assert site.requests == []


async def test_a_dossier_without_room_refuses_a_link_without_a_fetch() -> None:
    store = store_with(
        KEY, stored(1, "a" * 30_000), stored(2, "b" * (30_000 - MIN_SOURCE_CHARS + 1))
    )
    site = Site()
    assert await link(store, site) == Refused(RefusalReason.FULL)
    assert site.requests == []


async def test_a_replayed_link_adds_nothing_and_returns_the_stored_source() -> None:
    store, site = FakeStore(), Site()
    first = await link(store, site, message_id="7", link_index=2)
    again = await link(store, site, message_id="7", link_index=2)
    assert again == first
    assert len(await store.sources(KEY)) == 1
    assert len(site.requests) == 1  # the replay is recognized before the fetch


async def test_a_replay_in_a_full_dossier_returns_the_stored_source() -> None:
    store, site = FakeStore(), Site()
    first = await link(store, site, message_id="7")
    for n in range(2, MAX_SOURCES + 1):
        await add_text(store, KEY, "t" * 400 + str(n), message_id=f"text-{n}")
    again = await link(store, site, message_id="7")
    assert isinstance(first, Added) and isinstance(again, Added)
    assert again.position == first.position == 1
    assert again.source_count == MAX_SOURCES


@pytest.mark.parametrize(
    ("url", "expected"),
    [("ftp://example.org/a", InvalidLinkError), ("https://youtu.be/abc", UnsupportedLinkError)],
)
async def test_link_errors_propagate_before_any_read_or_fetch(
    url: str, expected: type[Exception]
) -> None:
    store, site = FakeStore(), Site()
    with pytest.raises(expected):
        await link(store, site, url)
    assert site.requests == [] and store.dossiers == {}


async def test_fetch_errors_propagate_unchanged() -> None:
    store = FakeStore()
    with pytest.raises(FetchError):
        await link(store, Site(status=404))
    assert await store.sources(KEY) == []


async def test_a_page_without_readable_text_propagates_unchanged() -> None:
    store = FakeStore()
    with pytest.raises(NoReadableTextError):
        await link(store, Site("<html><body><p>Trop court.</p></body></html>"))
    assert await store.sources(KEY) == []


# --- add_text --------------------------------------------------------------------------------

PASTED = (
    "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre régional, devant un public "
    "nombreux venu de toute la région.\n\n"
    "Douze films sont en compétition cette année, dont quatre premières oeuvres, selon les "
    "organisateurs qui annoncent aussi des ateliers pour les jeunes réalisateurs.\n"
    "La clôture est prévue le 10 mai avec la remise des prix par le jury international."
)


async def test_a_pasted_text_is_stripped_and_stored() -> None:
    store = FakeStore()
    outcome = await add_text(store, KEY, f"  {PASTED}\n ", message_id="5")
    [source] = await store.sources(KEY)
    assert (source.kind, source.url, source.text) == (SourceKind.PASTED_TEXT, None, PASTED)
    assert source.source_chars == len(PASTED)
    assert outcome == Added(
        position=1,
        kind=SourceKind.PASTED_TEXT,
        host=None,
        stored_chars=len(PASTED),
        truncated=False,
        paywall_suspected=False,
        source_count=1,
        total_chars=len(PASTED),
    )


async def test_a_short_pasted_text_is_not_stored() -> None:
    store = FakeStore()
    with pytest.raises(NoReadableTextError):
        await add_text(store, KEY, "  " + "a" * (MIN_SOURCE_CHARS - 1) + " ", message_id="5")
    assert store.dossiers == {}


async def test_a_long_pasted_text_is_capped() -> None:
    text = "Un paragraphe du texte collé par la journaliste, assez long pour compter.\n" * 600
    store = FakeStore()
    outcome = await add_text(store, KEY, text, message_id="5")
    [source] = await store.sources(KEY)
    assert isinstance(outcome, Added) and outcome.truncated
    assert source.text == cap_text(text.strip())[0]
    assert source.source_chars == len(text.strip())


async def test_a_replayed_text_adds_nothing() -> None:
    store = FakeStore()
    first = await add_text(store, KEY, PASTED, message_id="5")
    assert await add_text(store, KEY, PASTED, message_id="5") == first
    assert len(await store.sources(KEY)) == 1


async def test_the_same_text_in_two_messages_is_two_sources() -> None:
    store = FakeStore()
    await add_text(store, KEY, PASTED, message_id="5")
    second = await add_text(store, KEY, PASTED, message_id="6")
    assert isinstance(second, Added) and second.position == 2


async def test_a_full_dossier_refuses_a_text() -> None:
    store = store_with(KEY, *(stored(n, "a" * 500) for n in range(1, MAX_SOURCES + 1)))
    assert await add_text(store, KEY, PASTED, message_id="5") == Refused(RefusalReason.FULL)


async def test_dossiers_are_kept_per_key() -> None:
    store = FakeStore()
    await add_text(store, KEY, PASTED, message_id="5")
    other = await add_text(store, OTHER_KEY, PASTED, message_id="5")
    assert isinstance(other, Added) and other.position == 1


# --- clear and read --------------------------------------------------------------------------


async def test_clear_returns_the_number_of_sources_removed() -> None:
    store = store_with(KEY, stored(1, "a" * 500), stored(2, "b" * 500))
    assert await clear(store, KEY) == 2
    assert await read_state(store, KEY) == DossierState()
    assert await clear(store, KEY) == 0


async def test_reading_the_state_does_not_count_as_activity() -> None:
    store = store_with(KEY, stored(1, "a" * 500, url=URL), stored(2, "b" * 400))
    assert await read_state(store, KEY) == DossierState(
        count=2, total_chars=900, urls=frozenset({URL})
    )
    assert store.touches == []


# --- write_article ---------------------------------------------------------------------------


def one_paragraph(*ids: str, heading: str = "") -> DossierArticle:
    return DossierArticle(
        title="Station de dessalement",
        paragraphs=[
            HeadedParagraph(
                heading=heading,
                segments=[
                    SentenceSegment(
                        kind="sentence", text="Une station a été inaugurée.", evidence_ids=list(ids)
                    )
                ],
            )
        ],
    )


async def test_an_empty_dossier_calls_no_model() -> None:
    store, llm = FakeStore(), FakeLLM([])
    with pytest.raises(EmptyDossierError):
        await write_article(store, llm, KEY)
    assert llm.prompts == [] and store.touches == []


async def test_an_expired_dossier_is_empty() -> None:
    store = store_with(KEY, stored(1, "a" * 500))
    store.now = DOSSIER_TTL.total_seconds() / 3600 + 1
    llm = FakeLLM([])
    with pytest.raises(EmptyDossierError):
        await write_article(store, llm, KEY)
    assert llm.prompts == []


async def test_the_article_is_written_from_every_source_in_order() -> None:
    texts = ["Premier texte.\n" + "a" * 2_000, "Deuxième texte.\n" + "b" * 1_500]
    store = store_with(KEY, stored(2, texts[1]), stored(1, texts[0], url=URL))
    reply = one_paragraph("E1", "E3", heading="Partie")
    llm = FakeLLM([reply])

    written = await write_article(store, llm, KEY)

    assert written.evidence == build_dossier_evidence(texts)
    assert [source.position for source in written.sources] == [1, 2]
    assert written.budget == length_budget([len(text) for text in texts])
    assert written.result.article == reply
    assert written.paragraph_sources == [(1, 2)]
    [prompt] = llm.prompts
    assert prompt == build_dossier_prompt(
        written.evidence, max_words=written.budget.max_words, with_headings=True
    )
    assert f"dans la limite de {written.budget.max_words} mots" in prompt
    assert store.touches == [KEY]


async def test_a_thin_dossier_asks_for_plain_paragraphs() -> None:
    store = store_with(KEY, stored(1, "Un texte court.\n" + "a" * 1_000))
    llm = FakeLLM([one_paragraph("E1")])
    written = await write_article(store, llm, KEY)
    assert written.budget.thin is True
    assert "- Structure : des paragraphes simples, sans intertitre." in llm.prompts[0]


async def test_the_budget_uses_the_stored_lengths() -> None:
    long_source = replace(stored(1, "a" * 1_000), source_chars=90_000, truncated=True)
    store = store_with(KEY, long_source)
    written = await write_article(store, FakeLLM([one_paragraph("E1")]), KEY)
    assert written.budget == length_budget([1_000])


async def test_a_failed_generation_propagates_and_is_not_activity() -> None:
    store = store_with(KEY, stored(1, "a" * 500))
    bad = one_paragraph("E9")
    with pytest.raises(GenerationFailedError):
        await write_article(store, FakeLLM([bad, bad]), KEY)
    assert store.touches == []
