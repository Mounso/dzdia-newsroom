"""The prompt tryout of the v2 eval (K3a, ADR-0010 point 4): guard, metrics, picks, `select`,
`run` and the freeze verdict. No network: pages and feeds come from `httpx.MockTransport`."""

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from dzdia_newsroom.llm import FakeLLM
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.paths import Paths as BaselinePaths
from evals.baseline_v1.schemas import SampleItem, SnapshotMeta
from evals.baseline_v1.snapshot import sha256_text
from evals.v2.arms import plain_prompt_fingerprint, v2_prompt_fingerprint
from evals.v2.tryout import (
    AttemptRecord,
    Candidate,
    IssueDetailRecord,
    SampleGuard,
    TryoutError,
    TryoutPage,
    TryoutPaths,
    TryoutRun,
    count_numbers,
    count_quoted,
    counters,
    freeze_verdict,
    issue_lines,
    issues,
    latest_fingerprints,
    normalize_url,
    pick_pages,
    print_selection,
    run_id_for,
    run_tryout,
    select_pages,
    source_of,
)
from tests.tools.test_article_generation import BAD_QUOTE, GOOD_QUOTE, draft, quote, sentence

SAMPLE_DATE = "Tue, 27 Oct 2026 15:01:22 +0000"
SAMPLED_URL = "https://www.tsa-algerie.com/sampled-story/"
OPAQUE_URL = "https://www.tsa-algerie.com/?p=100"


def _sample() -> list[SampleItem]:
    original = SampleItem(
        item_id="tsa-00",
        source="tsa",
        url=SAMPLED_URL,
        title="Une histoire du panel",
        published=SAMPLE_DATE,
        snapshot_id="tsa-00",
    )
    opaque = original.model_copy(
        update={"item_id": "tsa-00-opaque", "url": OPAQUE_URL, "is_opaque": True}
    )
    return [original, opaque]


def _snapshots() -> list[SnapshotMeta]:
    return [
        SnapshotMeta(
            snapshot_id="tsa-00",
            url=SAMPLED_URL,
            final_url="https://www.tsa-algerie.com/sampled-story-final/",
            fetched_at=datetime(2026, 10, 27, tzinfo=UTC),
            html_sha256="h",
            text_sha256="t",
            n_chars=10,
        )
    ]


GUARD = SampleGuard.from_baseline(_sample(), _snapshots())


# --- guard -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.tsa-algerie.com/a/", "https://tsa-algerie.com/a"),
        ("HTTPS://WWW.TSA-Algerie.com/a", "https://tsa-algerie.com/a"),
        ("https://tsa-algerie.com/a/#comments", "https://tsa-algerie.com/a"),
        ("https://www.tsa-algerie.com/?p=100", "https://tsa-algerie.com?p=100"),
        ("https://tsa-algerie.com/A/", "https://tsa-algerie.com/A"),
    ],
)
def test_normalize_url(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        SAMPLED_URL,
        "https://tsa-algerie.com/sampled-story",
        "HTTPS://WWW.tsa-algerie.com/sampled-story/#top",
        "https://tsa-algerie.com/sampled-story-final",  # a snapshot's final URL
        "https://tsa-algerie.com/?p=100",  # an opaque sample row
    ],
)
def test_a_sampled_url_is_refused(url: str) -> None:
    assert GUARD.refusal(url) is not None


def test_a_final_url_landing_on_a_sampled_article_is_refused() -> None:
    url = "https://www.tsa-algerie.com/new-slug/"
    assert GUARD.refusal(url) is None
    assert GUARD.refusal(url, final_url="https://tsa-algerie.com/sampled-story") is not None


def test_the_query_is_kept() -> None:
    assert GUARD.refusal("https://tsa-algerie.com/?p=101") is None


def test_a_date_not_after_the_sample_is_refused() -> None:
    url = "https://www.tsa-algerie.com/other/"
    assert GUARD.refusal(url, published=SAMPLE_DATE) is not None
    assert GUARD.refusal(url, published="Tue, 27 Oct 2026 16:01:22 +0100") is not None  # same
    assert GUARD.refusal(url, published="Tue, 20 Oct 2026 10:00:00 +0000") is not None
    assert GUARD.refusal(url, published="Tue, 27 Oct 2026 15:01:23 +0000") is None


def test_an_unknown_date_is_kept() -> None:
    url = "https://www.tsa-algerie.com/other/"
    assert GUARD.refusal(url, published=None) is None
    assert GUARD.refusal(url, published="not a date") is None


def test_source_of() -> None:
    assert source_of("https://www.tsa-algerie.com/x/") == "tsa"
    assert source_of("https://algerie360.com/x/") == "algerie360"
    assert source_of("https://www.example.org/x/") == "example.org"


# --- metrics ---------------------------------------------------------------------------------


def test_count_quoted() -> None:
    text = (
        "Il a dit « nous reviendrons bientôt » puis « merci ».\n"
        '“Trois mots ici” et "encore trois mots" et "deux mots".\n'
        "Une marque « ouverte\nsur deux lignes » ne compte pas."
    )
    assert count_quoted(text) == 3


def test_count_numbers() -> None:
    assert count_numbers("Le 3 mai 2026, 300 000 m3 et 4,5 %.") == 7
    assert count_numbers("Aucun chiffre.") == 0


# --- picks -----------------------------------------------------------------------------------


def _candidate(
    url: str, source: str, *, quotes: int = 0, chars: int = 0, numbers: int = 0, day: int = 10
) -> Candidate:
    text = " ".join(["« un deux trois »"] * quotes + ["1"] * numbers + ["x" * max(chars, 1)])
    return Candidate(
        source=source,
        url=url,
        final_url=url,
        title=url,
        published=f"{day:02d} Nov 2026 10:00:00 +0000" if day else None,
        html="",
        text=text,
        fetched_at=datetime(2026, 11, 13, tzinfo=UTC),
    )


def test_picks_follow_the_criteria_order_and_the_quotas() -> None:
    tsa_quotes = _candidate("https://t/q", "tsa", quotes=9)
    tsa_long = _candidate("https://t/l", "tsa", chars=9000)  # tsa is out of quota by then
    a360_long = _candidate("https://a/l", "algerie360", chars=5000)
    a360_numbers = _candidate("https://a/n", "algerie360", numbers=50)
    other = _candidate("https://o/n", "elsewhere", numbers=90)  # no quota at all
    picks = pick_pages([tsa_long, a360_numbers, other, tsa_quotes, a360_long])
    assert [(c.url, why) for c, why in picks] == [
        ("https://t/q", "quotes"),
        ("https://a/l", "length"),
        ("https://a/n", "numbers"),
    ]


def test_a_page_is_picked_once() -> None:
    best = _candidate("https://a/1", "algerie360", quotes=5, chars=9000, numbers=50)
    second = _candidate("https://a/2", "algerie360", quotes=1, chars=10, numbers=1)
    picks = pick_pages([best, second], quotas={"algerie360": 2})
    assert [(c.url, why) for c, why in picks] == [
        ("https://a/1", "quotes"),
        ("https://a/2", "length"),
    ]


def test_ties_go_to_the_most_recent_then_to_the_url() -> None:
    old = _candidate("https://a/0", "algerie360", quotes=2, day=9)
    recent_b = _candidate("https://a/b", "algerie360", quotes=2, day=11)
    recent_a = _candidate("https://a/a", "algerie360", quotes=2, day=11)
    undated = _candidate("https://a/0-undated", "algerie360", quotes=2, day=0)
    picks = pick_pages([old, undated, recent_b, recent_a], criteria=["quotes"] * 4)
    assert [c.url for c, _ in picks] == ["https://a/a", "https://a/b"]  # quota of 2
    picks = pick_pages(
        [old, undated, recent_b, recent_a], quotas={"algerie360": 4}, criteria=["quotes"] * 4
    )
    assert [c.url for c, _ in picks] == [
        "https://a/a",
        "https://a/b",
        "https://a/0",
        "https://a/0-undated",
    ]


def test_a_criterion_with_no_eligible_candidate_picks_nothing() -> None:
    only = _candidate("https://t/1", "tsa", quotes=1)
    assert [why for _, why in pick_pages([only])] == ["quotes"]


# --- select ----------------------------------------------------------------------------------

FEEDS_TOML = """\
per_feed = 8
n_opaque = 4
opaque_feed = "tsa"
seed = 1

[[feeds]]
name = "tsa"
url = "https://www.tsa-algerie.com/feed/"

[[feeds]]
name = "algerie360"
url = "https://www.algerie360.com/feed/"
"""

FILLER = (
    "Le ministre a inauguré une nouvelle station de dessalement à Oran, selon un communiqué "
    "publié par le ministère de l'Hydraulique et repris par la presse locale."
)
QUOTED = "« Nous allons construire une autre station », a dit le ministre devant la presse."


def _html(title: str, paragraphs: Sequence[str]) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1>{body}"
        "</article></body></html>"
    )


def _rss(base: str, items: Sequence[tuple[str, str, str]]) -> str:
    entries = "".join(
        f"<item><title>{title}</title><link>{base}{slug}</link><pubDate>{date}</pubDate></item>"
        for slug, title, date in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{entries}</channel></rss>'


TSA = "https://www.tsa-algerie.com/"
A360 = "https://www.algerie360.com/"
TSA_ITEMS = [
    ("tsa-quotes/", "Citations à Oran", "Tue, 10 Nov 2026 10:00:00 +0000"),
    ("tsa-long/", "Long article", "Wed, 11 Nov 2026 10:00:00 +0000"),
    ("sampled-story", "Déjà dans le panel", "Thu, 12 Nov 2026 10:00:00 +0000"),
    ("old-story/", "Ancien article", "Tue, 20 Oct 2026 10:00:00 +0000"),
    ("redirected/", "Redirigé", "Thu, 12 Nov 2026 11:00:00 +0000"),
    ("excluded/", "Exclu", "Thu, 12 Nov 2026 12:00:00 +0000"),
]
A360_ITEMS = [
    ("a360-numbers/", "Chiffres", "Tue, 10 Nov 2026 10:00:00 +0000"),
    ("a360-paywall/", "Payant", "Wed, 11 Nov 2026 10:00:00 +0000"),
    ("a360-missing/", "Introuvable", "Wed, 11 Nov 2026 11:00:00 +0000"),
    ("a360-long/", "Long Algérie 360", "Mon, 09 Nov 2026 10:00:00 +0000"),
    ("a360-short/", "Court", "Mon, 09 Nov 2026 09:00:00 +0000"),
]
# Distinct paragraphs: the extraction drops repeated ones.
POINTS = [
    f"{FILLER} Point {word}."
    for word in ["un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit"]
]
PAGES: dict[str, str] = {
    f"{TSA}spike/": _html("Le titre du spike", [FILLER, QUOTED]),
    f"{TSA}tsa-quotes/": _html("Citations à Oran", [FILLER, QUOTED, QUOTED.replace("ne", "nes")]),
    f"{TSA}tsa-long/": _html("Long article", POINTS),
    OPAQUE_URL: _html("Déjà dans le panel", POINTS[:2]),
    f"{A360}a360-numbers/": _html("Chiffres", [FILLER, "En 2026, 300 000 m3 et 12 wilayas, 4 %."]),
    f"{A360}a360-paywall/": _html("Payant", [FILLER, "Réservé aux abonnés."]),
    f"{A360}a360-long/": _html("Long Algérie 360", POINTS[:5]),
    f"{A360}a360-short/": _html("Court", [FILLER]),
}


def _transport(requests: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if url == f"{TSA}feed/":
            return httpx.Response(200, text=_rss(TSA, TSA_ITEMS))
        if url == f"{A360}feed/":
            return httpx.Response(200, text=_rss(A360, A360_ITEMS))
        if url == f"{TSA}redirected/":
            return httpx.Response(301, headers={"Location": OPAQUE_URL})
        if url in PAGES:
            return httpx.Response(200, text=PAGES[url])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _paths(tmp_path: Path) -> TryoutPaths:
    baseline = BaselinePaths(dataset_dir=tmp_path / "baseline", data_dir=tmp_path / "bdata")
    baseline.dataset_dir.mkdir(parents=True)
    baseline.feeds.write_bytes(FEEDS_TOML.encode("utf-8"))
    write_jsonl(baseline.sample, _sample())
    write_jsonl(baseline.snapshots_meta, _snapshots())
    return TryoutPaths(
        dataset_dir=tmp_path / "datasets", data_dir=tmp_path / "data", baseline=baseline
    )


async def _no_sleep(_: float) -> None:
    return None


async def test_select_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paths = _paths(tmp_path)
    requests: list[str] = []
    async with httpx.AsyncClient(transport=_transport(requests), follow_redirects=True) as client:
        selection = await select_pages(
            paths,
            client,
            include=[f"{TSA}spike/"],
            exclude=[f"{TSA}excluded"],
            sleep=_no_sleep,
        )

    pages = read_jsonl(paths.pages, TryoutPage)
    assert pages == selection.pages
    assert [(p.page_id, p.url, p.picked_for, p.source) for p in pages] == [
        ("tryout-00", f"{TSA}spike/", "spike", "tsa"),
        ("tryout-01", f"{TSA}tsa-quotes/", "quotes", "tsa"),
        ("tryout-02", f"{A360}a360-long/", "length", "algerie360"),
        ("tryout-03", f"{A360}a360-numbers/", "numbers", "algerie360"),
    ]
    spike = pages[0]
    assert spike.title == "Le titre du spike" and spike.published is None  # from the metadata
    assert pages[1].title == "Citations à Oran"
    assert pages[1].published == "Tue, 10 Nov 2026 10:00:00 +0000"
    assert pages[1].n_quoted == 2

    for page in pages:
        text = paths.text_path(page.page_id).read_bytes()
        assert b"\r\n" not in text and b"\r\n" not in paths.html_path(page.page_id).read_bytes()
        assert sha256_text(text.decode("utf-8")) == page.text_sha256
        assert page.n_chars == len(text.decode("utf-8"))

    reasons = dict(selection.skipped)
    assert "baseline sample" in reasons[f"{TSA}sampled-story"]
    assert "not after" in reasons[f"{TSA}old-story/"]
    assert "final URL" in reasons[f"{TSA}redirected/"]
    assert reasons[f"{TSA}excluded/"] == "excluded by --exclude"
    assert "PaywallError" in reasons[f"{A360}a360-paywall/"]
    assert "404" in reasons[f"{A360}a360-missing/"]
    assert f"{TSA}sampled-story" not in requests and f"{TSA}old-story/" not in requests
    assert f"{TSA}excluded/" not in requests
    assert {c.url for c in selection.candidates} == {
        f"{TSA}tsa-quotes/",
        f"{TSA}tsa-long/",
        f"{A360}a360-numbers/",
        f"{A360}a360-long/",
        f"{A360}a360-short/",
    }

    print_selection(selection, _sample())
    out = capsys.readouterr().out
    assert "Une histoire du panel" in out and "construire" not in out and "dessalement" not in out


async def test_select_refuses_to_overwrite_the_page_list(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.pages.parent.mkdir(parents=True)
    paths.pages.write_bytes(b"")
    requests: list[str] = []
    async with httpx.AsyncClient(transport=_transport(requests)) as client:
        with pytest.raises(TryoutError, match="--force"):
            await select_pages(paths, client)
    assert requests == []


async def test_select_aborts_on_a_refused_include(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    async with httpx.AsyncClient(transport=_transport([]), follow_redirects=True) as client:
        with pytest.raises(TryoutError, match="refused"):
            await select_pages(paths, client, include=[f"{TSA}redirected/"], sleep=_no_sleep)
        with pytest.raises(TryoutError, match="failed"):
            await select_pages(paths, client, include=[f"{TSA}nowhere/"], sleep=_no_sleep)
    assert not paths.pages.exists()


# --- run -------------------------------------------------------------------------------------

SOURCE = (
    "Festival du film d’Oran\n"
    "Le festival s’est ouvert le 3 mai au théâtre régional.\n"
    "« Nous reviendrons l’an prochain », a déclaré Karim Ziani."
)
VALID = draft([sentence("Le festival s’est ouvert le 3 mai.", "E2"), quote(GOOD_QUOTE)])


def _frozen_page(paths: TryoutPaths, page_id: str = "tryout-00", text: str = SOURCE) -> TryoutPage:
    page = TryoutPage(
        page_id=page_id,
        source="tsa",
        url=f"{TSA}{page_id}/",
        final_url=f"{TSA}{page_id}/",
        title="Festival",
        picked_for="spike",
        fetched_at=datetime(2026, 11, 13, tzinfo=UTC),
        text_sha256=sha256_text(text),
        n_chars=len(text),
        n_quoted=0,
        n_numbers=1,
    )
    paths.snapshot_dir.mkdir(parents=True, exist_ok=True)
    paths.text_path(page_id).write_bytes(text.encode("utf-8"))
    return page


async def test_run_records_both_arms_and_is_idempotent(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths)])
    llm = FakeLLM([VALID, "Festival\n\nLe festival a ouvert le 3 mai."], model="m")
    records = await run_tryout(paths, llm)

    v2, plain = records
    assert read_jsonl(paths.runs, TryoutRun) == records
    assert v2.run_id == f"tryout-00:v2:{v2_prompt_fingerprint()[:12]}:m"
    assert plain.run_id == f"tryout-00:plain:{plain_prompt_fingerprint()[:12]}:m"
    assert v2.settings == {"temperature": 0.0, "response_schema": "ArticleDraft"}
    assert plain.settings == {"temperature": 0.0}
    assert (v2.prompt_version, plain.prompt_version) == ("v2-article-1", "v1-plain-1")
    assert v2.outcome == "ok" and v2.retried is False and v2.kept_quotes == 1
    assert v2.n_evidence == 3 and v2.n_sentences == 1 and v2.removed_quotes == 0
    assert v2.draft == VALID.model_dump(mode="json")
    assert v2.n_output_chars == len((v2.rendered_text or "").strip())
    assert [a.issues for a in v2.attempts] == [{}]
    assert plain.output_text == "Festival\n\nLe festival a ouvert le 3 mai."
    assert plain.n_output_chars == len("Festival\n\nLe festival a ouvert le 3 mai.")
    assert not v2.truncated and v2.n_source_chars == len(SOURCE)
    assert llm.temperatures == [0.0]  # the plain call; v2 goes through generate_structured

    assert await run_tryout(paths, FakeLLM([], model="m")) == []  # nothing left to do


async def test_run_records_the_issue_details(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths)])
    bad = draft([sentence("Le festival s’est ouvert.", "E2"), quote(BAD_QUOTE)])
    await run_tryout(paths, FakeLLM([bad, VALID], model="m"), arms=["v2"])
    [record] = read_jsonl(paths.runs, TryoutRun)
    assert [a.issue_details for a in record.attempts] == [
        [
            IssueDetailRecord(
                kind="quote_not_in_evidence", paragraph=0, segment=1, text=BAD_QUOTE, detail="E3"
            )
        ],
        [],
    ]


async def test_run_records_a_technical_error_and_retries_it(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths)])
    records = await run_tryout(paths, FakeLLM([TimeoutError("slow"), "Festival"], model="m"))
    assert records[0].error == "TimeoutError('slow')" and records[1].error is None

    retried = await run_tryout(paths, FakeLLM([VALID], model="m"))
    assert [(r.arm, r.error) for r in retried] == [("v2", None)]


async def test_run_aborts_when_a_text_changed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths)])
    paths.text_path("tryout-00").write_bytes(SOURCE.replace("\n", "\r\n").encode("utf-8"))
    llm = FakeLLM([], model="m")
    with pytest.raises(TryoutError, match="text_sha256"):
        await run_tryout(paths, llm)
    assert llm.prompts == []


async def test_run_filters_arms_and_pages(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths), _frozen_page(paths, "tryout-01")])
    records = await run_tryout(
        paths, FakeLLM(["Festival"], model="m"), arms=["plain"], page_ids=["tryout-01"]
    )
    assert [(r.page_id, r.arm) for r in records] == [("tryout-01", "plain")]
    with pytest.raises(TryoutError, match="Unknown page"):
        await run_tryout(paths, FakeLLM([]), page_ids=["tryout-09"])


# --- verdict ---------------------------------------------------------------------------------

PAGE_IDS = [f"tryout-{n:02d}" for n in range(6)]
NOW = datetime(2026, 11, 13, tzinfo=UTC)


def _run(page_id: str, arm: str, **update: Any) -> TryoutRun:
    record = TryoutRun(
        run_id=f"{page_id}:{arm}",
        page_id=page_id,
        arm="v2" if arm == "v2" else "plain",
        model="m",
        prompt_version="v",
        prompt_fingerprint=f"fp-{arm}",
        settings={"temperature": 0.0},
        created_at=NOW,
        truncated=False,
        n_source_chars=1000,
        n_output_chars=300,
    )
    if arm == "v2":
        record = record.model_copy(
            update={
                "outcome": "ok",
                "attempts": [AttemptRecord(issues={})],
                "retried": False,
                "removed_quotes": 0,
            }
        )
    return record.model_copy(update=update)


def _records(
    change: Callable[[int], dict[str, Any]] = lambda n: {},
) -> tuple[dict[str, TryoutRun], dict[str, TryoutRun]]:
    v2 = {p: _run(p, "v2", **change(n)) for n, p in enumerate(PAGE_IDS)}
    plain = {p: _run(p, "plain") for p in PAGE_IDS}
    return v2, plain


def _passed(v2: dict[str, TryoutRun], plain: dict[str, TryoutRun]) -> list[bool]:
    return [check.passed for check in freeze_verdict(PAGE_IDS, v2, plain).conditions]


def test_all_conditions_met_freezes() -> None:
    verdict = freeze_verdict(PAGE_IDS, *_records())
    assert verdict.status == "FREEZE" and verdict.missing == ()


def test_one_failed_v2_generation_revises() -> None:
    def failed_first(n: int) -> dict[str, Any]:
        return {"outcome": "failed", "n_output_chars": None} if n == 0 else {}

    v2, plain = _records(failed_first)
    assert _passed(v2, plain)[0] is False
    assert freeze_verdict(PAGE_IDS, v2, plain).status == "REVISE"


@pytest.mark.parametrize(("pages", "passed"), [(1, True), (2, False)])
def test_quotation_marks_at_the_first_attempt(pages: int, passed: bool) -> None:
    marked = [AttemptRecord(issues={"quotation_marks": 2}), AttemptRecord(issues={})]
    v2, plain = _records(lambda n: {"attempts": marked, "retried": True} if n < pages else {})
    assert _passed(v2, plain)[1] is passed
    second_only = [AttemptRecord(issues={"no_id": 1}), AttemptRecord(issues={"quotation_marks": 1})]
    v2, plain = _records(lambda n: {"attempts": second_only} if n < 3 else {})
    assert _passed(v2, plain)[1] is True  # only the first attempt counts


@pytest.mark.parametrize(("pages", "passed"), [(2, True), (3, False)])
def test_retry_used(pages: int, passed: bool) -> None:
    v2, plain = _records(lambda n: {"retried": True} if n < pages else {})
    assert _passed(v2, plain)[2] is passed
    expected = "FREEZE" if passed else "REVISE"
    assert freeze_verdict(PAGE_IDS, v2, plain).status == expected


@pytest.mark.parametrize(("removed", "passed"), [(1, True), (2, False)])
def test_removed_quotes_in_total(removed: int, passed: bool) -> None:
    v2, plain = _records(lambda n: {"removed_quotes": 1} if n < removed else {})
    assert _passed(v2, plain)[3] is passed


@pytest.mark.parametrize(("v2_chars", "passed"), [(200, True), (199, False)])
def test_median_length_ratio(v2_chars: int, passed: bool) -> None:
    v2, plain = _records(lambda n: {"n_output_chars": v2_chars})
    assert _passed(v2, plain)[4] is passed


def test_the_length_condition_uses_pages_with_an_output_in_both_arms() -> None:
    v2, plain = _records(lambda n: {"outcome": "failed", "n_output_chars": None} if n == 0 else {})
    plain["tryout-00"] = _run("tryout-00", "plain", n_output_chars=10_000)
    plain["tryout-01"] = _run("tryout-01", "plain", n_output_chars=450)
    # Paired pages 01-05: v2 300 each, plain 450, 300, 300, 300, 300: median 300.
    assert _passed(v2, plain)[4] is True


def test_missing_or_errored_records_are_incomplete() -> None:
    v2, plain = _records()
    del v2["tryout-02"]
    plain["tryout-03"] = _run("tryout-03", "plain", error="TimeoutError()", n_output_chars=None)
    verdict = freeze_verdict(PAGE_IDS, v2, plain)
    assert verdict.status == "INCOMPLETE"
    assert verdict.missing == ("tryout-02 v2", "tryout-03 plain")


def test_counters_use_the_latest_fingerprint_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths, p) for p in PAGE_IDS])
    v2, plain = _records()
    old = [
        r.model_copy(
            update={
                "run_id": f"{r.run_id}:old",
                "prompt_fingerprint": "fp-old",
                "created_at": NOW - timedelta(hours=1),
                "retried": True,
            }
        )
        for r in v2.values()
    ]
    write_jsonl(paths.runs, [*old, *v2.values(), *plain.values()])
    assert latest_fingerprints(read_jsonl(paths.runs, TryoutRun)) == {
        "v2": "fp-v2",
        "plain": "fp-plain",
    }
    assert counters(paths).status == "FREEZE"
    out = capsys.readouterr().out
    assert "fp-v2 (others present: fp-old)" in out and out.rstrip().endswith("FREEZE")
    assert counters(paths, fingerprint_v2="fp-old").status == "REVISE"


# --- flagged segments (decision U1) ----------------------------------------------------------


def _flagged(page_id: str, *attempts: AttemptRecord) -> TryoutRun:
    return _run(page_id, "v2", attempts=list(attempts), retried=len(attempts) > 1)


def _detail(kind: str, p: int | None, s: int | None, text: str, detail: str = "") -> Any:
    return IssueDetailRecord(kind=kind, paragraph=p, segment=s, text=text, detail=detail)


FLAGGED = {
    "tryout-00": _flagged(
        "tryout-00",
        AttemptRecord(
            issues={"quotation_marks": 1, "no_id": 1, "quote_not_in_evidence": 1},
            issue_details=[
                _detail("quotation_marks", None, None, "Le « Festival »"),
                _detail("no_id", 0, 0, "Il a\nplu."),
                _detail("quote_not_in_evidence", 1, 2, BAD_QUOTE, "E3"),
            ],
        ),
        AttemptRecord(
            issues={"unparsable": 1},
            raw_text='{"title": "secret',
            issue_details=[_detail("unparsable", None, None, "")],
        ),
    ),
    "tryout-01": _flagged("tryout-01", AttemptRecord(issues={})),
    "tryout-02": _run("tryout-02", "v2", error="TimeoutError()", attempts=[]),
}


def test_issue_lines_show_only_the_flagged_segments() -> None:
    assert issue_lines(PAGE_IDS, FLAGGED) == [
        "tryout-00 attempt 1 quotation_marks title - | Le « Festival »",
        "tryout-00 attempt 1 no_id p0.s0 - | Il a plu.",
        f"tryout-00 attempt 1 quote_not_in_evidence p1.s2 E3 | {BAD_QUOTE}",
        "tryout-00 attempt 2 unparsable raw text 17 chars",
        "tryout-01 no issue",
        "tryout-02 no attempt (technical error)",
    ]
    assert not any("secret" in line for line in issue_lines(PAGE_IDS, FLAGGED))


def test_issue_lines_filter_pages() -> None:
    assert issue_lines(PAGE_IDS, FLAGGED, only=["tryout-01", "tryout-05"]) == ["tryout-01 no issue"]


def test_issue_lines_on_an_old_record_without_details(tmp_path: Path) -> None:
    old = _run("tryout-00", "v2", attempts=[AttemptRecord(issues={"no_id": 2})]).model_dump(
        mode="json"
    )
    del old["attempts"][0]["issue_details"]
    path = tmp_path / "runs.jsonl"
    path.write_bytes((json.dumps(old) + "\n").encode("utf-8"))
    [record] = read_jsonl(path, TryoutRun)
    assert record.attempts[0].issue_details == []
    assert issue_lines(PAGE_IDS, {"tryout-00": record}) == [
        "tryout-00 attempt 1 no_id=2 (details not recorded)"
    ]


def test_the_issues_step_uses_the_latest_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    write_jsonl(paths.pages, [_frozen_page(paths, p) for p in PAGE_IDS])
    old = FLAGGED["tryout-00"].model_copy(
        update={"run_id": "old", "prompt_fingerprint": "fp-old", "created_at": NOW - timedelta(1)}
    )
    write_jsonl(paths.runs, [old, FLAGGED["tryout-01"]])
    assert issues(paths) == ["tryout-01 no issue"]
    assert "v2: fingerprint fp-v2 (others present: fp-old)" in capsys.readouterr().out
    assert issues(paths, "fp-old", ["tryout-00"])[0].startswith("tryout-00 attempt 1")
    with pytest.raises(TryoutError, match="Unknown page"):
        issues(paths, page_ids=["tryout-09"])


def test_run_id_format() -> None:
    assert run_id_for("tryout-00", "v2", "abcdef0123456789", "gemini") == (
        "tryout-00:v2:abcdef012345:gemini"
    )
