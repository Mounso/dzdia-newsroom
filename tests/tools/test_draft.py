"""The composition of the slice (ADR-0019, point 12): fetch, extract, cap, evidence, generate.
No real network (httpx.MockTransport) and no real model (FakeLLM)."""

from collections.abc import Callable

import httpx
import pytest
from dzdia_newsroom.tools.draft import Draft, draft_from_link, draft_from_text

from dzdia_newsroom.llm import FakeLLM
from dzdia_newsroom.tools.article import (
    ArticleDraft,
    GenerationFailedError,
    Paragraph,
    SentenceSegment,
    build_prompt,
)
from dzdia_newsroom.tools.evidence import MAX_INPUT_CHARS, build_evidence, cap_text
from dzdia_newsroom.tools.fetch_page import (
    BlockedUrlError,
    FetchError,
    FetchReason,
    InvalidLinkError,
    UnsupportedLinkError,
)
from dzdia_newsroom.tools.page_text import (
    MIN_SOURCE_CHARS,
    NoReadableTextError,
    extract_page_text,
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


def page(extra: str = "") -> str:
    return (
        f'<html><head><title>{TITLE}</title><meta property="og:title" content="{TITLE}">'
        f"</head><body><article><h1>{TITLE}</h1>\n{PARAGRAPHS}\n{extra}</article></body></html>"
    )


def article(*evidence_ids: str) -> ArticleDraft:
    return ArticleDraft(
        title="Station de dessalement",
        paragraphs=[
            Paragraph(
                segments=[
                    SentenceSegment(
                        kind="sentence",
                        text="Une station a été inaugurée à Oran.",
                        evidence_ids=list(evidence_ids or ("E1",)),
                    )
                ]
            )
        ],
    )


def html_handler(html: str, status: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, headers={"content-type": "text/html; charset=utf-8"}, text=html
        )

    return handler


async def link_draft(
    handler: Callable[[httpx.Request], httpx.Response],
    llm: FakeLLM,
    url: str = "https://example.org/article",
) -> Draft:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await draft_from_link(client, llm, url)


# --- link --------------------------------------------------------------------------------------


async def test_a_link_gives_a_validated_article_on_the_page_evidence() -> None:
    llm = FakeLLM([article("E1")])
    draft = await link_draft(html_handler(page()), llm)

    assert draft.result.article == article("E1")
    assert draft.result.removed_quotes == 0
    assert draft.paywall_suspected is False
    assert draft.truncated is False
    assert draft.evidence.items[0].text == TITLE
    assert any("450 millions de dollars" in item.text for item in draft.evidence.items)
    assert draft.source_chars == len(extract_page_text(page()).text)
    # Production sends the prompt that the eval measured: the evidence of the capped text.
    [prompt] = llm.prompts
    assert prompt == build_prompt(draft.evidence)


async def test_a_paywall_marker_is_carried_as_a_flag() -> None:
    llm = FakeLLM([article()])
    draft = await link_draft(html_handler(page("<p>Ce contenu est réservé aux abonnés.</p>")), llm)
    assert draft.paywall_suspected is True


async def test_a_long_page_is_capped_like_the_eval_does() -> None:
    long_paragraphs = "\n".join(
        "<p>"
        + f"Le chantier {n} avance selon le calendrier du ministère, bilan numéro {n}. " * 6
        + "</p>"
        for n in range(80)
    )
    html = (
        f"<html><head><title>{TITLE}</title></head><body><article><h1>{TITLE}</h1>"
        f"{long_paragraphs}</article></body></html>"
    )
    llm = FakeLLM([article()])
    draft = await link_draft(html_handler(html), llm)

    assert draft.truncated is True
    assert draft.source_chars > MAX_INPUT_CHARS
    assert sum(len(item.text) for item in draft.evidence.items) <= MAX_INPUT_CHARS


async def test_a_page_without_readable_text_raises_before_any_model_call() -> None:
    llm = FakeLLM([])
    with pytest.raises(NoReadableTextError):
        await link_draft(html_handler("<html><body><p>Trop court.</p></body></html>"), llm)
    assert llm.prompts == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ftp://example.org/a", InvalidLinkError),
        ("https://www.instagram.com/p/abc", UnsupportedLinkError),
    ],
)
async def test_link_errors_propagate_unchanged(url: str, expected: type[Exception]) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    with pytest.raises(expected):
        await link_draft(handler, FakeLLM([]), url)
    assert requests == []


async def test_fetch_errors_propagate_unchanged() -> None:
    with pytest.raises(FetchError) as raised:
        await link_draft(html_handler("", status=404), FakeLLM([]))
    assert raised.value.reason is FetchReason.NOT_FOUND


async def test_a_blocked_url_propagates_unchanged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise BlockedUrlError("host 'example.org' is not allowed")

    with pytest.raises(BlockedUrlError):
        await link_draft(handler, FakeLLM([]))


async def test_a_generation_failure_propagates_unchanged() -> None:
    llm = FakeLLM([article("E99"), article("E99")])
    with pytest.raises(GenerationFailedError):
        await link_draft(html_handler(page()), llm)


async def test_an_unexpected_model_error_propagates_unchanged() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        await link_draft(html_handler(page()), FakeLLM([RuntimeError("boom")]))


# --- pasted text -------------------------------------------------------------------------------

PASTED = (
    "Le festival du film d'Oran s'est ouvert le 3 mai au théâtre régional, devant un public "
    "nombreux venu de toute la région.\n\n"
    "Douze films sont en compétition cette année, dont quatre premières oeuvres, selon les "
    "organisateurs qui annoncent aussi des ateliers pour les jeunes réalisateurs.\n"
    "La clôture est prévue le 10 mai avec la remise des prix par le jury international."
)


async def test_a_pasted_text_goes_through_the_same_pipeline_from_the_evidence_step() -> None:
    llm = FakeLLM([article("E1", "E2")])
    draft = await draft_from_text(llm, f"  {PASTED}\n ")

    assert draft.result.article == article("E1", "E2")
    assert draft.evidence == build_evidence(cap_text(PASTED)[0])
    assert draft.source_chars == len(PASTED)
    assert draft.paywall_suspected is False
    assert draft.truncated is False
    assert llm.prompts == [build_prompt(draft.evidence)]


async def test_a_long_pasted_text_is_capped() -> None:
    text = ("Un paragraphe du texte collé par la journaliste, assez long pour compter.\n") * 600
    draft = await draft_from_text(FakeLLM([article()]), text)
    assert draft.truncated is True
    assert draft.evidence == build_evidence(cap_text(text.strip())[0])


async def test_a_short_pasted_text_has_no_readable_text() -> None:
    llm = FakeLLM([])
    with pytest.raises(NoReadableTextError):
        await draft_from_text(llm, "  " + "a" * (MIN_SOURCE_CHARS - 1) + "  ")
    assert llm.prompts == []


async def test_a_pasted_text_at_the_floor_is_accepted() -> None:
    draft = await draft_from_text(FakeLLM([article()]), "a" * MIN_SOURCE_CHARS)
    assert draft.source_chars == MIN_SOURCE_CHARS
