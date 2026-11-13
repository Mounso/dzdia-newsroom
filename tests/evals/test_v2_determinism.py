"""The temperature check of ADR-0013, point 10 (`python -m evals.v2.tryout determinism`): page
choice, temperatures sent, distinct outputs, failed v2 generations, idempotence, no output text
stored or printed. No network: `FakeLLM`."""

from pathlib import Path

import pytest

from dzdia_newsroom.llm import FakeLLM
from dzdia_newsroom.llm.fake import Reply
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.snapshot import sha256_text
from evals.v2 import arms, tryout
from evals.v2.arms import plain_prompt_fingerprint, v2_prompt_fingerprint
from evals.v2.determinism import (
    DeterminismRun,
    cmd_determinism,
    determinism_report,
    first_page_per_source,
    run_determinism,
)
from evals.v2.tryout import TryoutError, TryoutPage, TryoutPaths
from tests.evals.test_v2_tryout import SOURCE, _frozen_page, _paths
from tests.tools.test_article_generation import BAD_QUOTE, GOOD_QUOTE, draft, quote, sentence

V2_TITLE = "Titre secret de la sortie v2"
VALID = draft(
    [sentence("Le festival s’est ouvert le 3 mai.", "E2"), quote(GOOD_QUOTE)], title=V2_TITLE
)
INVALID = draft([sentence("Il a plu."), quote(BAD_QUOTE)])  # fails again after the retry
SAME = "Sortie secrète du bras plain."
OTHER = "Autre sortie secrète du bras plain."
MODEL = "m"


def _pages(paths: TryoutPaths, sources: list[str]) -> list[TryoutPage]:
    pages = [
        _frozen_page(paths, f"tryout-{n:02d}").model_copy(update={"source": source})
        for n, source in enumerate(sources)
    ]
    write_jsonl(paths.pages, pages)
    return pages


def _one_page(
    *, plain_hot: list[Reply] | None = None, v2: list[Reply] | None = None
) -> list[Reply]:
    """The replies for one page, in call order: plain at 0 three times, plain at 1.0 three
    times, then v2 three times."""
    return [SAME, SAME, SAME, *(plain_hot or [SAME, OTHER, SAME]), *(v2 or [VALID] * 3)]


def test_the_first_page_of_each_source_in_page_order(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    pages = _pages(paths, ["tsa", "tsa", "algerie360", "tsa", "algerie360"])
    assert [p.page_id for p in first_page_per_source(pages)] == ["tryout-00", "tryout-02"]


async def test_run_sends_the_temperatures_and_records_hashes_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa", "tsa", "algerie360", "algerie360"])
    llm = FakeLLM([*_one_page(), *_one_page()], model=MODEL)
    records = await run_determinism(paths, llm)

    assert len(records) == 18 and read_jsonl(paths.determinism, DeterminismRun) == records
    assert llm.temperatures == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0] * 2  # plain calls; v2 is structured
    assert [(r.page_id, r.arm, r.temperature, r.repeat) for r in records[:9]] == [
        ("tryout-00", arm, temperature, repeat)
        for arm, temperature in (("plain", 0.0), ("plain", 1.0), ("v2", 0.0))
        for repeat in range(3)
    ]
    assert {r.page_id for r in records} == {"tryout-00", "tryout-02"}
    plain_cold, plain_hot, v2 = records[0], records[3], records[6]
    assert plain_cold.settings == {"temperature": 0.0}
    assert plain_hot.settings == {"temperature": 1.0}
    assert v2.settings == {"temperature": 0.0, "response_schema": "ArticleDraft"}
    assert (plain_cold.prompt_version, v2.prompt_version) == ("v1-plain-1", "v2-article-1")
    assert plain_hot.prompt_fingerprint == plain_prompt_fingerprint()
    assert v2.prompt_fingerprint == v2_prompt_fingerprint()
    plain_fp, v2_fp = plain_prompt_fingerprint()[:12], v2_prompt_fingerprint()[:12]
    assert plain_cold.run_id == f"tryout-00:plain:t0:0:{plain_fp}:{MODEL}"
    assert records[5].run_id == f"tryout-00:plain:t1:2:{plain_fp}:{MODEL}"
    assert v2.run_id == f"tryout-00:v2:t0:0:{v2_fp}:{MODEL}"
    assert plain_cold.output_sha256 == sha256_text(SAME)
    assert plain_cold.n_output_chars == len(SAME)
    assert (plain_cold.outcome, v2.outcome, v2.retried) == (None, "ok", False)
    assert v2.output_sha256 is not None and v2.n_output_chars is not None

    stored = paths.determinism.read_bytes().decode("utf-8")
    assert all(text not in stored for text in (SAME, OTHER, V2_TITLE, GOOD_QUOTE, SOURCE))
    assert set(DeterminismRun.model_fields) == {
        "run_id", "page_id", "arm", "temperature", "repeat", "model", "prompt_version",
        "prompt_fingerprint", "settings", "created_at", "error", "output_sha256",
        "n_output_chars", "outcome", "retried",
    }  # fmt: skip

    v2_chars = v2.n_output_chars
    assert determinism_report(paths, MODEL) == [
        f"tryout-00 plain t=0: 3/3 repeats, 1 distinct, length {len(SAME)}-{len(SAME)}",
        f"tryout-00 plain t=1: 3/3 repeats, 2 distinct, length {len(SAME)}-{len(OTHER)}",
        f"tryout-00 v2    t=0: 3/3 repeats, 1 distinct, length {v2_chars}-{v2_chars}",
        f"tryout-02 plain t=0: 3/3 repeats, 1 distinct, length {len(SAME)}-{len(SAME)}",
        f"tryout-02 plain t=1: 3/3 repeats, 2 distinct, length {len(SAME)}-{len(OTHER)}",
        f"tryout-02 v2    t=0: 3/3 repeats, 1 distinct, length {v2_chars}-{v2_chars}",
        "plain t=0: identical outputs on every page (2 pages)",
        "plain t=1: different outputs on 2 of 2 pages (tryout-00, tryout-02)",
        "v2    t=0: identical outputs on every page (2 pages)",
    ]


async def test_identical_outputs_on_one_page_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa", "algerie360"])
    hot_same = _one_page(plain_hot=[SAME] * 3)
    await run_determinism(paths, FakeLLM([*hot_same, *_one_page()], model=MODEL))
    assert "plain t=1: different outputs on 1 of 2 pages (tryout-01)" in determinism_report(
        paths, MODEL
    )


@pytest.mark.parametrize(
    ("v2_replies", "distinct"),
    [
        ([VALID, INVALID, INVALID, VALID], 2),  # one failed generation (2 calls)
        ([INVALID] * 6, 3),  # each failed generation is a value of its own
    ],
)
async def test_a_failed_v2_generation_counts_as_its_own_value(
    tmp_path: Path, v2_replies: list[Reply], distinct: int
) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa"])
    records = await run_determinism(paths, FakeLLM(_one_page(v2=v2_replies), model=MODEL))
    v2 = [r for r in records if r.arm == "v2"]
    failed = [r for r in v2 if r.outcome == "failed"]
    assert failed and all(r.error is None and r.retried for r in failed)
    assert all(r.output_sha256 is None and r.n_output_chars is None for r in failed)
    [line] = [line for line in determinism_report(paths, MODEL) if "v2    t=0: 3/3" in line]
    assert f"{distinct} distinct" in line
    assert "v2    t=0: different outputs on 1 of 1 pages (tryout-00)" in determinism_report(
        paths, MODEL
    )


async def test_idempotent_and_a_technical_error_is_retried(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa"])
    replies = _one_page(plain_hot=[SAME, TimeoutError("slow"), SAME])
    records = await run_determinism(paths, FakeLLM(replies, model=MODEL))
    assert [r.error for r in records if r.error] == ["TimeoutError('slow')"]
    report = determinism_report(paths, MODEL)
    assert report[1] == (
        f"tryout-00 plain t=1: 2/3 repeats, 1 distinct, length {len(SAME)}-{len(SAME)} (incomplete)"
    )
    assert "plain t=1: incomplete (tryout-00)" in report

    llm = FakeLLM([OTHER], model=MODEL)
    [retried] = await run_determinism(paths, llm)
    assert (retried.temperature, retried.repeat, retried.error) == (1.0, 1, None)
    assert llm.temperatures == [1.0]
    assert await run_determinism(paths, FakeLLM([], model=MODEL)) == []
    assert len(read_jsonl(paths.determinism, DeterminismRun)) == 10  # the error record stays
    assert "plain t=1: different outputs on 1 of 1 pages (tryout-00)" in determinism_report(
        paths, MODEL
    )


async def test_a_changed_text_stops_the_step_before_any_call(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa"])
    paths.text_path("tryout-00").write_bytes(b"autre texte")
    llm = FakeLLM([], model=MODEL)
    with pytest.raises(TryoutError, match="text_sha256"):
        await run_determinism(paths, llm)
    assert llm.prompts == [] and not paths.determinism.exists()


@pytest.mark.parametrize("name", ["v2_prompt_fingerprint", "plain_prompt_fingerprint"])
async def test_a_prompt_that_differs_from_its_pin_stops_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa"])
    # Changed too: the fingerprints are checked first, before any page text is read.
    paths.text_path("tryout-00").write_bytes(b"autre texte")
    monkeypatch.setattr(arms, name, lambda: "f" * 64)
    llm = FakeLLM([], model=MODEL)
    with pytest.raises(TryoutError, match="differs from its pin"):
        await run_determinism(paths, llm)
    assert llm.prompts == [] and not paths.determinism.exists()


async def test_the_step_prints_counters_and_no_output_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa"])
    await cmd_determinism(paths, FakeLLM(_one_page(), model=MODEL))
    out = capsys.readouterr().out
    assert out.startswith(f"Model {MODEL}, pages tryout-00, 3 repeats per arm and temperature")
    assert "Executed 9 calls, 0 errors" in out
    assert "tryout-00 plain t=1 #1 ok, " in out
    assert all(text not in out for text in (SAME, OTHER, V2_TITLE, GOOD_QUOTE))


def test_the_step_is_registered_in_the_tryout_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    _pages(paths, ["tsa"])
    monkeypatch.setattr(tryout.TryoutPaths, "default", lambda: paths)
    monkeypatch.setattr(tryout, "_gemini", lambda: FakeLLM(_one_page(), model=MODEL))
    tryout.main(["determinism"])
    assert "v2    t=0: identical outputs on every page (1 pages)" in capsys.readouterr().out
    paths.text_path("tryout-00").write_bytes(b"autre texte")
    with pytest.raises(SystemExit, match="text_sha256"):
        tryout.main(["determinism"])
