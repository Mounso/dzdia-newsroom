"""The `run` step of the v2 comparison (ADR-0013, point 2): both arms on the non-opaque sample
items, idempotence, technical errors, the three guards, counters only. No network: `FakeLLM`."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dzdia_newsroom.llm import FakeLLM
from dzdia_newsroom.tools.evidence import cap_text
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.paths import Paths as BaselinePaths
from evals.baseline_v1.schemas import RunRecord, SampleItem, SnapshotMeta
from evals.baseline_v1.snapshot import sha256_text, text_path
from evals.v2 import arms
from evals.v2.compare import CompareError
from evals.v2.compare import __main__ as compare_main
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.run import (
    CompareRun,
    run_compare,
    run_line,
    summarize,
    summary_lines,
)
from tests.tools.test_article_generation import BAD_QUOTE, GOOD_QUOTE, draft, quote, sentence

SOURCE = (
    "Festival du film d’Oran\n"
    "Le festival s’est ouvert le 3 mai au théâtre régional.\n"
    "« Nous reviendrons l’an prochain », a déclaré Karim Ziani."
)
V2_TITLE = "Titre secret du bras v2"
V2_SENTENCE = "Le festival s’est ouvert le 3 mai."
VALID = draft([sentence(V2_SENTENCE, "E2"), quote(GOOD_QUOTE)], title=V2_TITLE)
INVALID = draft([sentence("Il a plu."), quote(BAD_QUOTE)])  # no id, quote not in evidence
PLAIN_TEXT = "Festival\n\nTexte secret du bras plain."
MODEL = "gemini-test"
NOW = datetime(2026, 11, 13, tzinfo=UTC)


def _item(item_id: str, *, opaque_of: str | None = None) -> SampleItem:
    return SampleItem(
        item_id=item_id,
        source="tsa",
        url=f"https://www.tsa-algerie.com/{item_id}/",
        title=f"Titre {item_id}",
        snapshot_id=opaque_of or item_id,
        is_opaque=opaque_of is not None,
    )


def _paths(
    tmp_path: Path,
    item_ids: tuple[str, ...] = ("tsa-00", "tsa-01"),
    *,
    text: str = SOURCE,
    baseline_runs: list[RunRecord] | None = None,
) -> ComparePaths:
    """A baseline with the given items, an opaque duplicate of the first, their snapshots and a
    successful `page_text / article` run per item with `MODEL`."""
    baseline = BaselinePaths(dataset_dir=tmp_path / "baseline", data_dir=tmp_path / "bdata")
    items = [_item(i) for i in item_ids]
    items.insert(1, _item(f"{item_ids[0]}-opaque", opaque_of=item_ids[0]))
    write_jsonl(baseline.sample, items)
    baseline.snapshot_dir.mkdir(parents=True)
    metas = []
    for item_id in item_ids:
        text_path(baseline.snapshot_dir, item_id).write_bytes(text.encode("utf-8"))
        metas.append(
            SnapshotMeta(
                snapshot_id=item_id,
                url=f"https://www.tsa-algerie.com/{item_id}/",
                final_url=f"https://www.tsa-algerie.com/{item_id}/",
                fetched_at=NOW,
                html_sha256="h",
                text_sha256=sha256_text(text),
                n_chars=len(text),
                reviewed=True,
            )
        )
    write_jsonl(baseline.snapshots_meta, metas)
    if baseline_runs is None:
        baseline_runs = [_baseline_run(i) for i in item_ids]
    write_jsonl(baseline.runs, baseline_runs)
    return ComparePaths(
        dataset_dir=tmp_path / "datasets", data_dir=tmp_path / "data", baseline=baseline
    )


def _baseline_run(item_id: str, **update: Any) -> RunRecord:
    record = RunRecord(
        run_id=f"{item_id}:page_text:article:0",
        item_id=item_id,
        condition="page_text",
        task="article",
        repeat=0,
        model=MODEL,
        prompt_version="v1-frozen",
        created_at=NOW,
        output_text="v1",
    )
    return record.model_copy(update=update)


# --- run -------------------------------------------------------------------------------------


async def test_run_records_both_arms_for_the_non_opaque_items(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    llm = FakeLLM([VALID, PLAIN_TEXT, VALID, PLAIN_TEXT], model=MODEL)
    records = await run_compare(paths, llm)

    assert [r.run_id for r in records] == [
        "tsa-00:v2:article:0",
        "tsa-00:plain_prompt:article:0",
        "tsa-01:v2:article:0",
        "tsa-01:plain_prompt:article:0",
    ]
    assert read_jsonl(paths.runs, CompareRun) == records
    v2, plain = records[:2]
    assert (v2.task, v2.repeat, plain.task, plain.repeat) == ("article", 0, "article", 0)
    assert (v2.model, plain.model) == (MODEL, MODEL)
    assert (v2.prompt_version, plain.prompt_version) == ("v2-article-1", "v1-plain-1")
    assert v2.prompt_fingerprint == arms.V2_PROMPT_FINGERPRINT
    assert plain.prompt_fingerprint == arms.PLAIN_PROMPT_FINGERPRINT
    assert v2.settings == {"temperature": 0.0, "response_schema": "ArticleDraft"}
    assert plain.settings == {"temperature": 0.0}
    for record in records:
        assert record.text_sha256 == sha256_text(SOURCE)
        assert not record.truncated and record.n_source_chars == len(SOURCE)
        assert record.error is None
    assert v2.outcome == "ok" and v2.retried is False and v2.kept_quotes == 1
    assert v2.draft == VALID.model_dump(mode="json")
    assert v2.n_output_chars == len((v2.rendered_text or "").strip())
    assert plain.output_text == PLAIN_TEXT and plain.n_output_chars == len(PLAIN_TEXT)
    assert llm.temperatures == [0.0, 0.0]  # the plain calls; v2 uses generate_structured
    assert SOURCE in llm.prompts[1] and SOURCE in llm.prompts[3]  # the plain prompts


async def test_run_is_idempotent(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    await run_compare(paths, FakeLLM([VALID, PLAIN_TEXT, VALID, PLAIN_TEXT], model=MODEL))
    llm = FakeLLM([], model=MODEL)
    assert await run_compare(paths, llm) == []
    assert llm.prompts == []
    assert len(read_jsonl(paths.runs, CompareRun)) == 4


async def test_both_arms_receive_the_capped_text_and_the_record_says_so(tmp_path: Path) -> None:
    long_text = SOURCE + "".join(f"\nLigne de remplissage numéro {n}." for n in range(2000))
    paths = _paths(tmp_path, ("tsa-00",), text=long_text)
    llm = FakeLLM([VALID, PLAIN_TEXT], model=MODEL)
    v2, plain = await run_compare(paths, llm)
    capped, truncated = cap_text(long_text)
    assert truncated
    for record in (v2, plain):
        assert record.truncated and record.n_source_chars == len(capped)
        assert record.text_sha256 == sha256_text(long_text)  # the full snapshot text
    assert capped in llm.prompts[1] and long_text not in llm.prompts[1]


async def test_a_technical_error_is_recorded_retried_and_kept(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ("tsa-00",))
    first = await run_compare(paths, FakeLLM([TimeoutError("slow"), PLAIN_TEXT], model=MODEL))
    assert [(r.condition, r.error) for r in first] == [
        ("v2", "TimeoutError('slow')"),
        ("plain_prompt", None),
    ]
    assert first[0].outcome is None and first[0].attempts == []

    retried = await run_compare(paths, FakeLLM([VALID], model=MODEL))
    assert [(r.condition, r.error, r.outcome) for r in retried] == [("v2", None, "ok")]
    in_file = read_jsonl(paths.runs, CompareRun)
    assert [(r.run_id, r.error is None) for r in in_file] == [
        ("tsa-00:v2:article:0", False),
        ("tsa-00:plain_prompt:article:0", True),
        ("tsa-00:v2:article:0", True),
    ]
    assert summarize(paths).error_records == 1
    assert await run_compare(paths, FakeLLM([], model=MODEL)) == []


async def test_a_failed_v2_generation_is_a_result_and_is_not_retried(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ("tsa-00",))
    [record] = await run_compare(paths, FakeLLM([INVALID, INVALID], model=MODEL), conditions=["v2"])
    assert record.error is None and record.outcome == "failed" and record.retried is True
    assert record.rendered_text is None and record.n_output_chars is None
    assert len(record.attempts) == 2
    assert await run_compare(paths, FakeLLM([], model=MODEL), conditions=["v2"]) == []


async def test_conditions_and_items_filter(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ("tsa-00", "tsa-01", "tsa-02"))
    records = await run_compare(
        paths,
        FakeLLM([PLAIN_TEXT, PLAIN_TEXT], model=MODEL),
        conditions=["plain_prompt"],
        item_ids=["tsa-02", "tsa-00"],
    )
    assert [r.run_id for r in records] == [
        "tsa-00:plain_prompt:article:0",
        "tsa-02:plain_prompt:article:0",
    ]
    # Given in another order, the conditions run in call order: v2 first.
    records = await run_compare(
        paths,
        FakeLLM([VALID], model=MODEL),
        conditions=["plain_prompt", "v2"],
        item_ids=["tsa-00"],
    )
    assert [r.run_id for r in records] == ["tsa-00:v2:article:0"]


@pytest.mark.parametrize("item_id", ["tsa-09", "tsa-00-opaque"])
async def test_an_unknown_or_opaque_item_is_refused(tmp_path: Path, item_id: str) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(CompareError, match="Unknown or opaque"):
        await run_compare(paths, FakeLLM([], model=MODEL), item_ids=[item_id])


# --- guards ----------------------------------------------------------------------------------


async def _refused(paths: ComparePaths, match: str, model: str = MODEL) -> None:
    llm = FakeLLM([], model=model)
    with pytest.raises(CompareError, match=match):
        await run_compare(paths, llm)
    assert llm.prompts == []
    assert not paths.runs.exists()


@pytest.mark.parametrize("name", ["v2_prompt_fingerprint", "plain_prompt_fingerprint"])
async def test_refuses_a_prompt_that_differs_from_its_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(arms, name, lambda: "f" * 64)
    await _refused(_paths(tmp_path), "differs from its pin")


async def test_refuses_a_snapshot_text_that_changed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    changed = SOURCE.replace("3 mai", "4 mai")
    text_path(paths.baseline.snapshot_dir, "tsa-01").write_bytes(changed.encode("utf-8"))
    await _refused(paths, "text_sha256")


async def test_refuses_a_missing_snapshot_text(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    text_path(paths.baseline.snapshot_dir, "tsa-01").unlink()
    await _refused(paths, "Missing snapshot text")


async def test_refuses_a_snapshot_without_meta(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    metas = read_jsonl(paths.baseline.snapshots_meta, SnapshotMeta)
    write_jsonl(paths.baseline.snapshots_meta, metas[:1])
    await _refused(paths, "Missing snapshot: no meta for tsa-01")


async def test_the_latest_meta_of_a_snapshot_is_the_reference(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ("tsa-00",))
    [meta] = read_jsonl(paths.baseline.snapshots_meta, SnapshotMeta)
    write_jsonl(
        paths.baseline.snapshots_meta, [meta, meta.model_copy(update={"text_sha256": "0" * 64})]
    )
    await _refused(paths, "text_sha256")


async def test_refuses_a_model_other_than_the_baseline_one(tmp_path: Path) -> None:
    await _refused(_paths(tmp_path), "differs from the baseline", model="gemini-other")


async def test_the_model_check_reads_the_successful_page_text_article_runs(
    tmp_path: Path,
) -> None:
    others = [
        _baseline_run("tsa-00", run_id="a", model="other-1", condition="url_only"),
        _baseline_run("tsa-00", run_id="b", model="other-2", task="summary"),
        _baseline_run("tsa-00", run_id="c", model="other-3", error="TimeoutError()"),
    ]
    paths = _paths(tmp_path, ("tsa-00",), baseline_runs=[*others, _baseline_run("tsa-00")])
    records = await run_compare(paths, FakeLLM([VALID, PLAIN_TEXT], model=MODEL))
    assert len(records) == 2

    two_models = [_baseline_run("tsa-00"), _baseline_run("tsa-01", model="other")]
    await _refused(_paths(tmp_path / "two", baseline_runs=two_models), "differs from the baseline")


async def test_refuses_when_the_baseline_has_no_page_text_article_run(tmp_path: Path) -> None:
    runs = [
        _baseline_run("tsa-00", condition="url_only"),
        _baseline_run("tsa-01", error="TimeoutError()"),
    ]
    await _refused(_paths(tmp_path, baseline_runs=runs), "No successful page_text / article")


# --- output ----------------------------------------------------------------------------------


def _run_cli(
    paths: ComparePaths, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, *argv: str
) -> None:
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    monkeypatch.setattr(compare_main, "_gemini", lambda: llm)
    compare_main.main(["run", *argv])


def test_the_cli_prints_counters_and_no_model_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    llm = FakeLLM([VALID, PLAIN_TEXT, INVALID, INVALID, PLAIN_TEXT], model=MODEL)
    _run_cli(paths, llm, monkeypatch)
    out = capsys.readouterr().out

    lines = out.splitlines()
    assert lines[0] == f"Model {MODEL}, conditions v2, plain_prompt"
    assert lines[1].startswith("tsa-00 v2           ok, retried no, issues -, kept 1, removed 0")
    assert lines[2].startswith(f"tsa-00 plain_prompt {len(PLAIN_TEXT)} chars")
    assert lines[3].startswith("tsa-01 v2           failed, retried yes")
    assert lines[5:] == [
        "Executed 4 runs, 0 technical errors (re-run the step to retry).",
        "v2           ok 1, failed 1, technical errors 0, missing 0; retried 1, quotes kept 1, "
        "removed 0",
        "plain_prompt ok 2, technical errors 0, missing 0",
        "Error records in the runs file: 0",
    ]
    records = read_jsonl(paths.runs, CompareRun)
    model_texts = [PLAIN_TEXT, "Texte secret", V2_TITLE, V2_SENTENCE, GOOD_QUOTE, BAD_QUOTE]
    model_texts += [r.rendered_text for r in records if r.rendered_text]
    assert all(text not in out for text in model_texts)


def test_the_cli_filters_conditions_and_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    llm = FakeLLM([VALID], model=MODEL)
    _run_cli(paths, llm, monkeypatch, "--conditions", "v2", "--items", "tsa-01")
    assert [r.run_id for r in read_jsonl(paths.runs, CompareRun)] == ["tsa-01:v2:article:0"]
    out = capsys.readouterr().out
    assert "plain_prompt" not in out
    assert "v2           ok 1, failed 0, technical errors 0, missing 0" in out


def test_the_cli_prints_a_refusal_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(SystemExit, match="differs from the baseline"):
        _run_cli(paths, FakeLLM([], model="gemini-other"), monkeypatch)


def test_the_cli_rejects_an_unknown_condition() -> None:
    with pytest.raises(SystemExit):
        compare_main.main(["run", "--conditions", "page_text"])


def _record(item_id: str, condition: str, **update: Any) -> CompareRun:
    record = CompareRun(
        run_id=f"{item_id}:{condition}:article:0",
        item_id=item_id,
        condition="v2" if condition == "v2" else "plain_prompt",
        model=MODEL,
        prompt_version="v",
        prompt_fingerprint="fp",
        settings={"temperature": 0.0},
        created_at=NOW,
        text_sha256="t",
        truncated=False,
        n_source_chars=100,
        n_output_chars=50,
    )
    if condition == "v2":
        record = record.model_copy(
            update={"outcome": "ok", "retried": False, "kept_quotes": 0, "removed_quotes": 0}
        )
    return record.model_copy(update=update)


ITEMS = ("tsa-00", "tsa-01", "tsa-02", "tsa-03")
FAILED: dict[str, Any] = {"outcome": "failed", "kept_quotes": None, "removed_quotes": None}


def test_the_summary_counts_the_latest_record_of_every_planned_run(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ITEMS)
    error = {"error": "TimeoutError()", "n_output_chars": None}
    write_jsonl(
        paths.runs,
        [
            _record("tsa-00", "v2", retried=True, kept_quotes=2, removed_quotes=1),
            _record("tsa-01", "v2", **FAILED),
            _record("tsa-02", "v2", **error),
            _record("tsa-02", "v2", **FAILED),  # retried after the error
            _record("tsa-03", "v2", **FAILED),
            _record("tsa-00", "plain_prompt"),
            _record("tsa-01", "plain_prompt", **error),
            _record("tsa-02", "plain_prompt"),
            _record("tsa-09", "plain_prompt", **error),  # not a planned run
        ],
    )
    summary = summarize(paths)
    assert summary.failed_v2 == 3
    assert summary_lines(summary) == [
        "v2           ok 1, failed 3, technical errors 0, missing 0; retried 1, quotes kept 2, "
        "removed 1",
        "plain_prompt ok 2, technical errors 1, missing 1",
        "Error records in the runs file: 3",
        "3 v2 generations failed (more than 2): the guard of ADR-0010, point 12 applies, no "
        "headline comparison until the cause is understood.",
    ]
    filtered = summary_lines(summarize(paths, ["plain_prompt"], ["tsa-00", "tsa-03"]))
    assert filtered[0] == "plain_prompt ok 1, technical errors 0, missing 1"


def test_two_failed_v2_generations_do_not_trigger_the_guard(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ITEMS)
    write_jsonl(
        paths.runs,
        [
            _record("tsa-00", "v2", **FAILED),
            _record("tsa-01", "v2", **FAILED),
            _record("tsa-02", "v2"),
            _record("tsa-03", "v2"),
        ],
    )
    lines = summary_lines(summarize(paths, ["v2"]))
    assert not any("ADR-0010" in line for line in lines)


def test_run_line_has_no_model_text() -> None:
    record = _record("tsa-00", "plain_prompt", output_text=PLAIN_TEXT, latency_ms=1500)
    assert run_line(record) == "tsa-00 plain_prompt 50 chars, 1.5 s"
    error = _record("tsa-00", "v2", error="TimeoutError('slow')")
    assert run_line(error) == "tsa-00 v2           ERROR TimeoutError('slow')"
