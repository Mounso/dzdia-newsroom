"""The `guard-replay` step of the v2 comparison (ADR-0014, point 7): the quote guard applied to
the recorded v2 drafts, with no model call and no file written, counters only. Synthetic data
and files under `tmp_path` only."""

import inspect
from pathlib import Path
from typing import Any

import pytest

from evals.baseline_v1.jsonl import write_jsonl
from evals.baseline_v1.schemas import HumanLabel, Label
from evals.baseline_v1.snapshot import text_path
from evals.v2.compare import CompareError
from evals.v2.compare import __main__ as compare_main
from evals.v2.compare.guard_replay import ArticleReplay, guard_replay, replay_lines
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.run import CompareRun
from tests.evals.test_v2_compare_labels import _element
from tests.evals.test_v2_compare_run import FAILED, _paths, _record
from tests.tools.test_article_generation import (
    BAD_QUOTE,
    GOOD_QUOTE,
    NARRATION,
    draft,
    quote,
    sentence,
)

ITEMS = ("tsa-00", "tsa-01", "tsa-02")
OPENING = sentence("Le festival s’est ouvert le 3 mai.", "E2")
# tsa-00: one quote inside quotation marks; tsa-01: one inside, one outside (E2 is narration).
DRAFTS = {
    "tsa-00": draft([OPENING, quote(GOOD_QUOTE)]),
    "tsa-01": draft([OPENING, quote(GOOD_QUOTE)], [quote(NARRATION, evidence_id="E2")]),
}

V2_Q0 = _element("tsa-00", "v2", "Citation secrète zéro", "quote")
V2_Q0_PENDING = _element("tsa-00", "v2", "Citation secrète en attente", "quote")
V2_Q1 = _element("tsa-01", "v2", "Citation secrète un", "quote")
V2_Q1_BAD = _element("tsa-01", "v2", "Citation secrète mal attribuée", "quote")
V2_PERSON = _element("tsa-01", "v2", "Personne Secrète")  # not a quote
PLAIN_Q0 = _element("tsa-00", "plain_prompt", "Citation secrète du bras plain", "quote")
ELEMENTS = [V2_Q0, V2_Q0_PENDING, V2_Q1, V2_Q1_BAD, V2_PERSON, PLAIN_Q0]


def _lab(element_id: str, label: Label, note: str = "") -> HumanLabel:
    return HumanLabel(element_id=element_id, label=label, note=note)


LABELS = [
    _lab(V2_Q0.element_id, "NOT_IN_SOURCE"),
    _lab(V2_Q0.element_id, "SUPPORTED"),  # the latest label counts
    _lab(V2_Q1.element_id, "SUPPORTED"),
    _lab(V2_Q1_BAD.element_id, "CONTRADICTED", "locuteur"),
    _lab(V2_PERSON.element_id, "NOT_IN_SOURCE"),
    _lab(PLAIN_Q0.element_id, "NOT_IN_SOURCE"),
]


def _v2(item_id: str, **update: Any) -> CompareRun:
    return _record(item_id, "v2", **{"prompt_version": "v2-article-1", **update})


def _files(tmp_path: Path, records: list[CompareRun] | None = None) -> ComparePaths:
    paths = _paths(tmp_path, ITEMS)
    if records is None:
        records = [
            _v2("tsa-00", draft=DRAFTS["tsa-00"].model_dump(mode="json"), kept_quotes=1),
            _v2("tsa-01", error="TimeoutError()", n_output_chars=None),
            _v2("tsa-01", draft=DRAFTS["tsa-01"].model_dump(mode="json"), kept_quotes=2),
            _v2("tsa-02", retried=True, **FAILED),
            _record("tsa-00", "plain_prompt", output_text="Texte secret du bras plain."),
        ]
    write_jsonl(paths.runs, records)
    write_jsonl(paths.elements, ELEMENTS)
    write_jsonl(paths.labels, LABELS)
    return paths


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_quotes_inside_marks_are_kept_and_quotes_outside_are_rejected(tmp_path: Path) -> None:
    replay = guard_replay(_files(tmp_path))
    assert [(a.item_id, a.kept, a.rejected) for a in replay.articles] == [
        ("tsa-00", 1, 0),
        ("tsa-01", 2, 1),
    ]


def test_the_labels_of_each_article_s_v2_quotes(tmp_path: Path) -> None:
    replay = guard_replay(_files(tmp_path))
    assert replay.articles == (
        ArticleReplay("tsa-00", 1, 0, {"S": 1, "C": 0, "N": 0, "pending": 1}),
        ArticleReplay("tsa-01", 2, 1, {"S": 1, "C": 1, "N": 0, "pending": 0}),
    )


def test_the_totals_and_the_lines(tmp_path: Path) -> None:
    replay = guard_replay(_files(tmp_path))
    assert (replay.kept, replay.rejected) == (3, 1)
    assert (replay.with_quotes_before, replay.with_quotes_after) == (2, 2)
    assert replay.failed == ("tsa-02",)
    assert replay.prompt_versions == ("v2-article-1",)
    assert replay_lines(replay) == [
        "Quote guard of ADR-0014 replayed on the recorded v2 drafts: no model call, no file "
        "written.",
        "tsa-00 quotes kept 1, rejected 0; labels of the v2 quotes: S 1, C 0, N 0, pending 1",
        "tsa-01 quotes kept 2, rejected 1; labels of the v2 quotes: S 1, C 1, N 0, pending 0",
        "tsa-02 failed generation, not replayed",
        "Replayed 2 articles: quotes kept 3, rejected 1; articles with at least one quote: 2 "
        "before the rule, 2 after.",
        "Prompt versions of the replayed records: v2-article-1",
    ]


def test_an_article_losing_every_quote_counts_before_but_not_after(tmp_path: Path) -> None:
    only_narration = draft([OPENING, quote(NARRATION, evidence_id="E2")])
    records = [
        _v2("tsa-00", draft=DRAFTS["tsa-00"].model_dump(mode="json")),
        _v2("tsa-01", draft=only_narration.model_dump(mode="json")),
        _v2("tsa-02", draft=draft([OPENING]).model_dump(mode="json"), prompt_version="v2-x"),
    ]
    replay = guard_replay(_files(tmp_path, records))
    assert [(a.kept, a.rejected) for a in replay.articles] == [(1, 0), (1, 1), (0, 0)]
    assert (replay.with_quotes_before, replay.with_quotes_after) == (2, 1)
    assert replay.failed == ()
    assert replay.prompt_versions == ("v2-article-1", "v2-x")


@pytest.mark.parametrize("drop", ["missing", "error"])
def test_an_article_without_a_usable_record_is_refused(tmp_path: Path, drop: str) -> None:
    records = [_v2(i, draft=draft([OPENING]).model_dump(mode="json")) for i in ITEMS[:2]]
    if drop == "error":
        records.append(_v2("tsa-02", error="TimeoutError()", n_output_chars=None))
    with pytest.raises(CompareError, match="tsa-02"):
        guard_replay(_files(tmp_path, records))


def test_a_draft_that_no_longer_validates_is_refused_without_its_text(tmp_path: Path) -> None:
    broken = draft([sentence("Il a plu."), quote(BAD_QUOTE), quote(NARRATION, evidence_id="E2")])
    records = [
        _v2("tsa-00", draft=DRAFTS["tsa-00"].model_dump(mode="json")),
        _v2("tsa-01", draft=broken.model_dump(mode="json")),
        _v2("tsa-02", **FAILED),
    ]
    with pytest.raises(CompareError) as info:
        guard_replay(_files(tmp_path, records))
    message = str(info.value)
    assert "tsa-01" in message and "no_id=1,quote_not_in_evidence=1" in message
    assert "quote_not_quoted_in_source" not in message
    assert BAD_QUOTE not in message and "Il a plu" not in message


def test_an_unreadable_draft_is_refused_without_its_text(tmp_path: Path) -> None:
    records = [
        _v2("tsa-00", draft={"title": "Titre secret", "paragraphs": "pas une liste"}),
        _v2("tsa-01", **FAILED),
        _v2("tsa-02", **FAILED),
    ]
    with pytest.raises(CompareError, match="tsa-00") as info:
        guard_replay(_files(tmp_path, records))
    assert "secret" not in str(info.value)


def test_the_snapshot_texts_are_checked(tmp_path: Path) -> None:
    paths = _files(tmp_path)
    text_path(paths.baseline.snapshot_dir, "tsa-01").write_bytes("Texte changé.".encode())
    with pytest.raises(CompareError, match="text_sha256"):
        guard_replay(paths)


def test_the_step_prints_counters_only_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _files(tmp_path)
    before = _tree(tmp_path)
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    compare_main.main(["guard-replay"])
    out = capsys.readouterr().out
    assert _tree(tmp_path) == before  # data, dataset and baseline directories unchanged
    assert out.splitlines() == replay_lines(guard_replay(paths))
    hidden = [GOOD_QUOTE, NARRATION, "Karim Ziani", "secret", "Secrète", "locuteur"]
    hidden += [e.element_id for e in ELEMENTS] + [e.value for e in ELEMENTS]
    assert all(text not in out for text in hidden)


def test_the_replay_takes_no_client() -> None:
    assert list(inspect.signature(guard_replay).parameters) == ["paths"]
