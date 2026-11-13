"""The `report` step of the v2 comparison (ADR-0013, points 8 and 9; ADR-0010, points 9 to 12):
the paired bootstrap, per-arm statistics, the conclusion, the guards and the rendering. Synthetic
data and files under `tmp_path` only."""

import math
import random
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evals.baseline_v1.jsonl import write_jsonl
from evals.baseline_v1.report import cluster_bootstrap_ci
from evals.baseline_v1.schemas import ElementRecord, ExtractionRecord, HumanLabel, Label
from evals.v2.compare import __main__ as compare_main
from evals.v2.compare.labels import SEED, CheckRecord, ExportRecord, guide_sha256, sheet_key
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.report import (
    ArmData,
    ArmOutput,
    ElementRatio,
    PairedDiff,
    ReportData,
    ValidatorCounters,
    arm_stats,
    build_report,
    conclusion,
    difference,
    element_ratio,
    fmt_diff,
    fmt_rate,
    fmt_ratio,
    labels_changed,
    load_report_data,
    paired_bootstrap_diff,
    render_report,
)
from evals.v2.records import AttemptRecord
from tests.evals.test_v2_compare_labels import _element
from tests.evals.test_v2_compare_run import FAILED, NOW, _baseline_run, _paths, _record

# --- the paired bootstrap --------------------------------------------------------------------


def _reference(
    a: Sequence[tuple[int, int]], b: Sequence[tuple[int, int]], n_boot: int, seed: int
) -> tuple[list[float], int]:
    """The resampling of the brief, written out: the estimates kept, sorted, and the skipped."""
    rng = random.Random(seed)
    n = len(a)
    kept: list[float] = []
    skipped = 0
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        den_a, den_b = sum(a[i][1] for i in idx), sum(b[i][1] for i in idx)
        if not den_a or not den_b:
            skipped += 1
            continue
        kept.append(sum(a[i][0] for i in idx) / den_a - sum(b[i][0] for i in idx) / den_b)
    return sorted(kept), skipped


def test_identical_arms_give_zero_with_a_zero_interval() -> None:
    clusters = [(1, 3), (0, 2), (2, 4)]
    diff = paired_bootstrap_diff(clusters, clusters)
    assert diff == PairedDiff(0.0, 0.0, 0.0, 3, 0)


def test_the_same_indices_are_applied_to_both_arms() -> None:
    # b counts twice what a counts on every article: with the same indices both ratios of sums
    # are equal and every resample gives exactly 0; independent draws would not.
    a = [(1, 1), (0, 1), (1, 2)]
    b = [(2, 2), (0, 2), (2, 4)]
    assert paired_bootstrap_diff(a, b) == PairedDiff(0.0, 0.0, 0.0, 3, 0)


def test_the_bootstrap_follows_the_baseline_convention_and_is_seeded() -> None:
    # a has no element when every index is 0 or 1, b when every index is 1 or 3.
    a = [(0, 0), (0, 0), (2, 3), (0, 1)]
    b = [(1, 1), (0, 0), (1, 4), (0, 0)]
    for seed in (0, 7):
        kept, skipped = _reference(a, b, 500, seed)
        diff = paired_bootstrap_diff(a, b, n_boot=500, seed=seed)
        assert diff.skipped == skipped and skipped > 0
        assert len(kept) + skipped == 500
        assert diff.low == kept[int(0.025 * (len(kept) - 1))]
        assert diff.high == kept[int(0.975 * (len(kept) - 1))]
        assert diff.estimate == pytest.approx(2 / 4 - 2 / 5)  # pooled, not resampled
        assert diff.n_pairs == 4
    assert paired_bootstrap_diff(a, b) == paired_bootstrap_diff(a, b)  # seed 0 by default


def test_resamples_without_an_element_in_one_arm_are_skipped_and_counted() -> None:
    a = [(0, 0), (1, 1)]  # only resamples drawing the first article twice have no element
    b = [(1, 1), (1, 1)]
    _, expected = _reference(a, b, 10_000, 0)
    diff = paired_bootstrap_diff(a, b)
    assert diff.skipped == expected
    assert 2_200 < diff.skipped < 2_800  # about a quarter
    assert diff.estimate == 0.0


def test_no_paired_article_gives_no_estimate() -> None:
    diff = paired_bootstrap_diff([], [])
    assert diff.n_pairs == 0 and math.isnan(diff.estimate) and math.isnan(diff.low)
    assert fmt_diff(diff) == "n/a"
    with pytest.raises(ValueError):
        paired_bootstrap_diff([(0, 1)], [])


# --- arms in memory --------------------------------------------------------------------------


def _lab(e: ElementRecord, label: Label, note: str = "") -> HumanLabel:
    return HumanLabel(element_id=e.element_id, label=label, note=note)


def _arm(
    name: str,
    outputs: Sequence[tuple[str, Sequence[ElementRecord]]],
    labels: Sequence[HumanLabel] = (),
    **update: Any,
) -> ArmData:
    return ArmData(
        arm=name,  # type: ignore[arg-type]
        n_items=3,
        outputs=tuple(ArmOutput(item_id, 100, tuple(els)) for item_id, els in outputs),
        labels={lab.element_id: lab for lab in labels},
        **update,
    )


def _labeled(
    name: str, outputs: Sequence[tuple[str, Sequence[tuple[ElementRecord, Label]]]]
) -> ArmData:
    """An arm whose every element has the given label."""
    return _arm(
        name,
        [(item_id, [e for e, _ in pairs]) for item_id, pairs in outputs],
        [_lab(e, label) for _, pairs in outputs for e, label in pairs],
    )


def _el(item_id: str, arm: str, value: str, type_: str = "person") -> ElementRecord:
    return _element(item_id, arm, value, type_)  # type: ignore[arg-type]


def test_a_single_arm_rate_counts_an_article_without_element_as_0_0() -> None:
    a, b = _el("tsa-00", "v2", "Ali"), _el("tsa-00", "v2", "Oran", "place")
    labels = [_lab(a, "SUPPORTED"), _lab(b, "NOT_IN_SOURCE")]
    stats = arm_stats(_arm("v2", [("tsa-00", [a, b]), ("tsa-01", [])], labels))
    assert stats.unsupported == cluster_bootstrap_ci([(1, 2), (0, 0)], n_boot=10_000, seed=0)
    assert (stats.unsupported.numerator, stats.unsupported.denominator) == (1, 2)
    articles = stats.articles_with_unsupported
    assert articles == cluster_bootstrap_ci([(1, 1), (0, 1)], n_boot=10_000, seed=0)
    assert (articles.numerator, articles.denominator) == (1, 2)


def test_rate_without_quotes_contradicted_rate_and_quotes() -> None:
    person = _el("tsa-00", "v2", "Ali")
    quote = _el("tsa-00", "v2", "Nous reviendrons", "quote")
    place = _el("tsa-01", "v2", "Oran", "place")
    arm = _labeled(
        "v2",
        [("tsa-00", [(person, "SUPPORTED"), (quote, "NOT_IN_SOURCE")]),
         ("tsa-01", [(place, "CONTRADICTED")])],
    )  # fmt: skip
    stats = arm_stats(arm)
    assert (stats.unsupported.numerator, stats.unsupported.denominator) == (2, 3)
    assert (stats.without_quotes.numerator, stats.without_quotes.denominator) == (1, 2)
    assert (stats.contradicted.numerator, stats.contradicted.denominator) == (1, 3)
    assert stats.quotes_stated == 1
    assert (stats.quotes_unsupported.numerator, stats.quotes_unsupported.denominator) == (1, 1)


def test_a_misattributed_quote_is_a_contradicted_quote_noted_locuteur() -> None:
    quotes = [_el("tsa-00", "v2", f"Citation {n}", "quote") for n in range(4)]
    person = _el("tsa-00", "v2", "Ali")
    labels = [
        _lab(quotes[0], "CONTRADICTED", "Mauvais LOCUTEUR"),  # counted
        _lab(quotes[1], "CONTRADICTED", "mots changés"),
        _lab(quotes[2], "NOT_IN_SOURCE", "locuteur"),
        _lab(quotes[3], "SUPPORTED", "locuteur"),
        _lab(person, "CONTRADICTED", "locuteur"),  # not a quote
    ]
    stats = arm_stats(_arm("v2", [("tsa-00", [*quotes, person])], labels))
    assert stats.misattributed == 1 and stats.quotes_stated == 4


def test_an_incomplete_arm_has_no_statistic() -> None:
    a = _el("tsa-00", "v2", "Ali")
    pending = _arm("v2", [("tsa-00", [a])])
    assert not pending.complete and pending.pending == 1
    with pytest.raises(ValueError, match="not complete"):
        arm_stats(pending)
    assert not _arm("v2", []).complete  # no output yet
    assert not _arm("v2", [("tsa-00", [])], unextracted=1).complete  # not extracted yet
    assert difference(pending, _arm("page_text", [("tsa-00", [])])) is None


def test_the_point_estimate_uses_the_paired_articles_only() -> None:
    pt = [_el(i, "page_text", "Ali") for i in ("tsa-00", "tsa-01", "tsa-02")]
    page_text = _labeled(
        "page_text",
        [("tsa-00", [(pt[0], "SUPPORTED")]), ("tsa-01", [(pt[1], "NOT_IN_SOURCE")]),
         ("tsa-02", [(pt[2], "NOT_IN_SOURCE")])],
    )  # fmt: skip
    v2 = [_el(i, "v2", "Ali") for i in ("tsa-00", "tsa-01")]
    arm = _labeled("v2", [("tsa-00", [(v2[0], "SUPPORTED")]), ("tsa-01", [(v2[1], "SUPPORTED")])])
    diff = difference(arm, page_text)
    assert diff is not None
    assert diff.n_pairs == 2
    assert diff.estimate == pytest.approx(0 / 2 - 1 / 2)  # tsa-02 is left out


def test_the_element_ratio_and_the_two_thirds_guard() -> None:
    assert not ElementRatio(2, 3, 2).below_two_thirds  # exactly two thirds
    assert ElementRatio(1, 2, 1).below_two_thirds
    assert ElementRatio(65, 98, 16).below_two_thirds
    assert not ElementRatio(0, 0, 0).below_two_thirds
    assert fmt_ratio(ElementRatio(2, 3, 2)) == "66.7 % (2/3 on 2 articles)"
    assert fmt_ratio(ElementRatio(1, 2, 1)) == (
        "50.0 % (1/2 on 1 articles): fewer than two thirds of the elements of page_text, a lower "
        "rate may come from stating less (ADR-0010, point 12)"
    )
    assert fmt_ratio(ElementRatio(0, 0, 0)) == "n/a"

    arm = _arm(
        "v2", [("tsa-00", [_el("tsa-00", "v2", "A")]), ("tsa-02", [_el("tsa-02", "v2", "B")] * 5)]
    )
    page_text = _arm(
        "page_text",
        [("tsa-00", [_el("tsa-00", "page_text", v) for v in "ABC"]),
         ("tsa-01", [_el("tsa-01", "page_text", "D")] * 4)],
    )  # fmt: skip
    assert element_ratio(arm, page_text) == ElementRatio(1, 3, 1)  # shared article only


# --- conclusion ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        (
            PairedDiff(-0.05, -0.08, -0.01, 16, 0),
            "v2 lowers the unsupported rate by 5.0 points [1.0; 8.0] on the article task.",
        ),
        (
            PairedDiff(-0.05, -0.08, 0.0, 16, 3),  # upper end 0: not entirely below zero
            "No detectable difference on 16 articles: v2 − page_text = −5.0 points [−8.0; 0.0].",
        ),
        (
            PairedDiff(0.03, 0.0, 0.06, 15, 0),  # lower end 0: not entirely above zero
            "No detectable difference on 15 articles: v2 − page_text = +3.0 points [0.0; 6.0].",
        ),
        (
            PairedDiff(0.03, 0.01, 0.06, 16, 0),
            "v2 is worse on the article task: v2 − page_text = +3.0 points [1.0; 6.0]. The cause "
            "must be investigated before any real use.",
        ),
        (None, "No conclusion is given until every label is in."),
    ],
)
def test_the_conclusion_is_chosen_from_the_interval(diff: PairedDiff | None, expected: str) -> None:
    assert conclusion(diff, 0) == expected


def test_the_first_guard_gives_no_conclusion() -> None:
    lowering = PairedDiff(-0.05, -0.08, -0.01, 13, 0)
    assert conclusion(lowering, 3) == (
        "3 v2 generations failed validation (more than 2): the guard of ADR-0010, point 12 "
        "applies, and no conclusion is given until the cause is understood."
    )
    assert conclusion(lowering, 2).startswith("v2 lowers the unsupported rate")
    assert conclusion(None, 3) == "No conclusion is given until every label is in."


def test_formats() -> None:
    assert fmt_rate(cluster_bootstrap_ci([(0, 0)])) == "n/a"
    rate = fmt_rate(cluster_bootstrap_ci([(1, 4), (1, 4)], n_boot=10))
    assert rate == "25.0 % [25.0–25.0 %] (2/8)"
    assert fmt_diff(PairedDiff(-0.024, -0.06, 0.011, 16, 0)) == "−2.4 points [−6.0; 1.1]"
    assert fmt_diff(PairedDiff(-0.0004, -0.01, 0.01, 16, 0)) == "0.0 points [−1.0; 1.0]"


def test_labels_changed_counts_each_element_once() -> None:
    records = [
        HumanLabel(element_id="a", label="SUPPORTED"),
        HumanLabel(element_id="a", label="SUPPORTED", note="relu"),  # note only
        HumanLabel(element_id="b", label="SUPPORTED"),
        HumanLabel(element_id="b", label="CONTRADICTED"),
        HumanLabel(element_id="b", label="SUPPORTED"),  # changed twice: counted once
        HumanLabel(element_id="c", label="NOT_IN_SOURCE"),
        HumanLabel(element_id="d", label="SUPPORTED"),
        HumanLabel(element_id="e", label="CONTRADICTED"),
        HumanLabel(element_id="d", label="NOT_IN_SOURCE"),
    ]
    assert labels_changed(records) == 2
    assert labels_changed([]) == 0


# --- rendering from memory -------------------------------------------------------------------

COUNTERS = ValidatorCounters(0, {}, 0, 0, 0, 0)


def _data(arms: Sequence[ArmData] | None = None, **update: Any) -> ReportData:
    data = ReportData(
        arms=tuple(arms or [_arm(name, []) for name in ("page_text", "plain_prompt", "v2")]),
        validator=COUNTERS,
        exports=(),
        current_guide_sha256="a" * 64,
        checks=(),
        n_label_records=0,
        labels_changed=0,
    )
    return replace(data, **update)


def _section(report: str, title: str) -> list[str]:
    """The lines of one `## ` section, without its title."""
    lines = report.splitlines()
    start = lines.index(f"## {title}") + 1
    end = next((n for n in range(start, len(lines)) if lines[n].startswith("## ")), len(lines))
    return lines[start:end]


def _row(lines: list[str], arm: str) -> list[str]:
    [row] = [line for line in lines if line.startswith(f"| {arm} |")]
    return [cell.strip() for cell in row.strip("|").split("|")]


def _three_arms(v2_pending: bool = False) -> list[ArmData]:
    def pair(arm: str, second: Label) -> list[tuple[str, list[tuple[ElementRecord, Label]]]]:
        return [
            ("tsa-00", [(_el("tsa-00", arm, "Ali"), "SUPPORTED")]),
            ("tsa-01", [(_el("tsa-01", arm, "Oran", "place"), second)]),
        ]

    v2 = _labeled("v2", pair("v2", "SUPPORTED"))
    if v2_pending:
        v2 = replace(v2, labels=dict(list(v2.labels.items())[:1]))
    return [
        _labeled("page_text", pair("page_text", "NOT_IN_SOURCE")),
        _labeled("plain_prompt", pair("plain_prompt", "CONTRADICTED")),
        v2,
    ]


def test_an_incomplete_arm_shows_no_rate_no_count_per_type_no_difference_no_conclusion() -> None:
    report = render_report(_data(_three_arms(v2_pending=True)))

    status = _row(_section(report, "Status"), "v2")
    assert status == ["v2", "2 of 3", "0", "0", "2", "1", "1"]
    results = _row(_section(report, "Results per arm"), "v2")
    assert results[1:5] == ["labels pending"] * 4
    assert results[5:7] == ["2", "100"]  # element count and length: no label involved
    assert _row(_section(report, "Results per arm"), "plain_prompt")[1].startswith("50.0 %")
    assert _row(_section(report, "Quotes"), "v2")[1:] == ["labels pending"] * 5
    assert _row(_section(report, "Quotes"), "page_text")[1:4] == ["0", "n/a", "0"]

    differences = "\n".join(_section(report, "Differences of the unsupported rate"))
    assert "| v2 − page_text (primary) | labels pending | labels pending | labels pending |" in (
        differences
    )
    assert "| v2 − plain_prompt | labels pending |" in differences
    assert "| plain_prompt − page_text | 0.0 points [0.0; 0.0] | 2 | 0 |" in differences
    assert "A gain is attributed to the architecture only for the part v2 − plain_prompt" in (
        differences
    )
    assert [line for line in _section(report, "Conclusion") if line] == [
        "No conclusion is given until every label is in."
    ]


def test_complete_arms_show_the_differences_and_the_conclusion() -> None:
    report = render_report(_data(_three_arms()))
    differences = "\n".join(_section(report, "Differences of the unsupported rate"))
    assert "| v2 − page_text (primary) | −50.0 points [" in differences
    assert "| v2 − plain_prompt | −50.0 points [" in differences
    [sentence] = [line for line in _section(report, "Conclusion") if line]
    assert sentence.startswith(("v2 lowers", "No detectable difference on 2 articles"))


def test_the_first_guard_in_the_report() -> None:
    arms = _three_arms()
    arms[2] = replace(arms[2], failed=3)
    report = render_report(_data(arms))
    [sentence] = [line for line in _section(report, "Conclusion") if line]
    assert sentence.startswith("3 v2 generations failed validation (more than 2)")
    assert _row(_section(report, "Status"), "v2")[2] == "3"


def test_the_two_thirds_flag_in_the_report() -> None:
    arms = _three_arms()
    arms[2] = replace(arms[2], outputs=arms[2].outputs[:1])  # one element against two
    cell = _row(_section(render_report(_data(arms)), "Results per arm"), "v2")[7]
    assert cell == "100.0 % (1/1 on 1 articles)"  # tsa-00 only: 1 against 1
    extra = _el("tsa-00", "page_text", "Extra")
    first, second = arms[0].outputs
    arms[0] = replace(
        arms[0],
        outputs=(replace(first, elements=(*first.elements, extra)), second),
        labels={**arms[0].labels, extra.element_id: _lab(extra, "SUPPORTED")},
    )
    cell = _row(_section(render_report(_data(arms)), "Results per arm"), "v2")[7]
    assert cell.startswith("50.0 % (1/2 on 1 articles): fewer than two thirds")


def _export(guide: str) -> ExportRecord:
    return ExportRecord(
        exported_at=NOW, seed=SEED, n_rows=6, rows_per_item={"tsa-00": 6}, guide_sha256=guide
    )


GUIDE_CHANGED = "**The labeling guide changed after the first export** (ADR-0010, point 8)."


@pytest.mark.parametrize(
    ("exports", "current", "changed"),
    [
        ((), "a" * 64, False),
        (("a" * 64,), "a" * 64, False),
        (("a" * 64, "a" * 64), "a" * 64, False),
        (("a" * 64,), "b" * 64, True),  # the guide changed after the only export
        (("a" * 64, "b" * 64), "b" * 64, True),  # two exports with different hashes
        (("a" * 64,), None, False),  # missing guide: said on its own line
    ],
)
def test_the_guide_changed_line(
    exports: tuple[str, ...], current: str | None, changed: bool
) -> None:
    data = _data(exports=tuple(_export(h) for h in exports), current_guide_sha256=current)
    settings = _section(render_report(data), "Settings")
    assert (GUIDE_CHANGED in settings) is changed
    for h in exports:
        assert f"- 2026-11-13 00:00 UTC: seed {SEED}, 6 rows, guide SHA-256 {h}" in settings
    if not exports:
        assert "No label sheet exported yet." in settings
    if current is None:
        assert "The labeling guide file is missing." in settings


def test_the_label_checks_section() -> None:
    check = CheckRecord(
        checked_at=NOW,
        n_labels=10,
        quote_supported_not_found=1,
        inconsistent_values=0,
        locuteur_notes=2,
    )
    section = _section(
        render_report(_data(checks=(check, check), n_label_records=12, labels_changed=3)),
        "Label checks",
    )
    assert section.count("| 2026-11-13 00:00 UTC | 10 | 1 | 0 | 2 |") == 2
    assert "Elements whose label value changed in the comparison's labels file: 3." in section
    empty = _section(render_report(_data()), "Label checks")
    assert "No label check run yet." in empty
    assert "No comparison label imported yet." in empty


# --- loading from files ----------------------------------------------------------------------

ITEMS = ("tsa-00", "tsa-01", "tsa-02")
PT_TEXTS = {"tsa-00": " Article secret A. \n", "tsa-01": "Article secret BB.", "tsa-02": "C"}

PT0 = _element("tsa-00", "page_text", "Ali Benali")
PT0_QUOTE = _element("tsa-00", "page_text", "Nous reviendrons", "quote")
PT1 = _element("tsa-01", "page_text", "Oran", "place")
PT2 = _element("tsa-02", "page_text", "quatre mille dinars", "number")
URL_ONLY = _element("tsa-00", "url_only", "Karim Ziani")
PLAIN0 = _element("tsa-00", "plain_prompt", "Ali Benali")
PLAIN1 = _element("tsa-01", "plain_prompt", "Tlemcen", "place")
V2_0 = _element("tsa-00", "v2", "Nous reviendrons", "quote")
V2_1 = _element("tsa-01", "v2", "Oran", "place")
ALL_ELEMENTS = [PT0, PT0_QUOTE, PT1, PT2, URL_ONLY, PLAIN0, PLAIN1, V2_0, V2_1]

COMPARE_LABELS = [
    _lab(PT1, "SUPPORTED"),  # the baseline says NOT_IN_SOURCE: the comparison wins
    _lab(PT0_QUOTE, "CONTRADICTED", "locuteur"),
    _lab(PT2, "SUPPORTED"),
    _lab(PLAIN0, "CONTRADICTED"),
    _lab(PLAIN0, "SUPPORTED"),  # changed
    _lab(PLAIN1, "NOT_IN_SOURCE"),
    _lab(V2_0, "SUPPORTED"),
    _lab(V2_1, "SUPPORTED"),
    _lab(V2_1, "SUPPORTED", "relu"),  # note only
]
RENDERED = "Article secret rendu du bras v2."


def _extraction(run_id: str, model: str) -> ExtractionRecord:
    return ExtractionRecord(
        run_id=run_id, model=model, thinking_level="high", prompt_version="x-1", created_at=NOW
    )


def _files(tmp_path: Path, *, compare_labels: bool = True) -> ComparePaths:
    runs = [_baseline_run(i, output_text=PT_TEXTS[i]) for i in ITEMS]
    paths = _paths(tmp_path, ITEMS, baseline_runs=runs)
    paths = replace(paths, labeling_guide=tmp_path / "guide.md")
    paths.labeling_guide.write_bytes(b"# Guide\n")
    baseline = paths.baseline
    write_jsonl(baseline.extractions, [_extraction(r.run_id, "extractor-old") for r in runs])
    write_jsonl(baseline.elements, [PT0, PT0_QUOTE, PT1, PT2, URL_ONLY])
    write_jsonl(baseline.labels, [_lab(PT0, "SUPPORTED"), _lab(PT1, "NOT_IN_SOURCE")])

    error = {"error": "TimeoutError()", "n_output_chars": None}
    v2_ok: dict[str, Any] = {"rendered_text": RENDERED, "draft": {"title": RENDERED}}
    write_jsonl(
        paths.runs,
        [
            _record("tsa-00", "plain_prompt", n_output_chars=40, output_text="Article secret P."),
            _record("tsa-01", "plain_prompt", n_output_chars=60, output_text="Article secret Q."),
            _record("tsa-02", "plain_prompt", **error),  # still open
            _record(
                "tsa-00",
                "v2",
                n_output_chars=30,
                retried=True,
                kept_quotes=1,
                removed_quotes=1,
                attempts=[
                    AttemptRecord(issues={"no_id": 2, "quote_not_in_evidence": 1}),
                    AttemptRecord(issues={"quote_not_in_evidence": 1}),
                ],
                **v2_ok,
            ),
            _record("tsa-01", "v2", **error),  # retried below
            _record(
                "tsa-01", "v2", n_output_chars=31, attempts=[AttemptRecord(issues={})], **v2_ok
            ),
            _record(
                "tsa-02",
                "v2",
                retried=True,
                attempts=[AttemptRecord(issues={"unknown_id": 1})] * 2,
                **FAILED,
            ),
        ],
    )
    write_jsonl(
        paths.extractions,
        [
            _extraction(f"{i}:{c}:article:0", "extractor-new")
            for i in ("tsa-00", "tsa-01")
            for c in ("plain_prompt", "v2")
        ],
    )
    write_jsonl(paths.elements, [PLAIN0, PLAIN1, V2_0, V2_1])
    if compare_labels:
        write_jsonl(paths.labels, COMPARE_LABELS)
    write_jsonl(paths.export_log, [_export(guide_sha256(paths.labeling_guide))])
    check = CheckRecord(
        checked_at=NOW,
        n_labels=7,
        quote_supported_not_found=0,
        inconsistent_values=1,
        locuteur_notes=0,
    )
    write_jsonl(paths.check_log, [check])
    return paths


def test_page_text_reads_both_label_files_and_the_latest_comparison_label_wins(
    tmp_path: Path,
) -> None:
    data = load_report_data(_files(tmp_path))
    page_text = data.arm("page_text")
    assert [o.item_id for o in page_text.outputs] == list(ITEMS)
    assert [o.n_chars for o in page_text.outputs] == [len(PT_TEXTS[i].strip()) for i in ITEMS]
    assert page_text.labels[PT0.element_id].label == "SUPPORTED"  # from the baseline only
    assert page_text.labels[PT1.element_id].label == "SUPPORTED"  # the comparison wins
    assert page_text.complete and page_text.elements == [PT0, PT0_QUOTE, PT1, PT2]
    stats = arm_stats(page_text)
    assert (stats.unsupported.numerator, stats.unsupported.denominator) == (1, 4)
    assert stats.misattributed == 1
    assert page_text.models == ("gemini-test",) and page_text.prompt_versions == ("v1-frozen",)
    assert page_text.extractors == ("extractor-old (high)",)

    plain = data.arm("plain_prompt")
    assert plain.labels[PLAIN0.element_id].label == "SUPPORTED"  # the latest one
    assert PT0.element_id not in plain.labels  # the baseline's labels are for page_text only
    assert (len(plain.outputs), plain.open_errors, plain.complete) == (2, 1, True)
    assert data.labels_changed == 1 and data.n_label_records == len(COMPARE_LABELS)


def test_a_failed_v2_generation_removes_the_article_from_the_paired_differences(
    tmp_path: Path,
) -> None:
    data = load_report_data(_files(tmp_path))
    v2 = data.arm("v2")
    assert [o.item_id for o in v2.outputs] == ["tsa-00", "tsa-01"]
    assert (v2.failed, v2.open_errors, v2.complete) == (1, 0, True)
    assert [o.n_chars for o in v2.outputs] == [30, 31]
    diff = difference(v2, data.arm("page_text"))
    assert diff is not None and diff.n_pairs == 2
    assert diff.estimate == pytest.approx(0 / 2 - 1 / 3)  # tsa-02 of page_text is left out


def test_the_validator_counters(tmp_path: Path) -> None:
    counters = load_report_data(_files(tmp_path)).validator
    assert counters == ValidatorCounters(
        generations=3,  # the technical error retried on tsa-01 is not one
        first_attempt_issues={"no_id": 2, "quote_not_in_evidence": 1, "unknown_id": 1},
        retried=2,
        failed=1,
        kept_quotes=1,
        removed_quotes=1,
    )
    section = _section(build_report(_files(tmp_path / "again")), "Validator counters of v2")
    assert "| First-attempt issues: no_id | 2 |" in section
    assert "| First-attempt issues: empty | 0 |" in section
    assert "| Runs that used the retry | 2 |" in section
    assert "| Generations that failed validation | 1 |" in section


def test_the_report_from_files(tmp_path: Path) -> None:
    report = build_report(_files(tmp_path))
    status = _section(report, "Status")
    assert _row(status, "page_text") == ["page_text", "3 of 3", "—", "0", "4", "4", "0"]
    assert _row(status, "plain_prompt") == ["plain_prompt", "2 of 3", "—", "1", "2", "2", "0"]
    assert _row(status, "v2") == ["v2", "2 of 3", "1", "0", "2", "2", "0"]
    results = _section(report, "Results per arm")
    assert _row(results, "v2")[5:] == ["2", "30.5", "66.7 % (2/3 on 2 articles)"]
    assert _row(results, "plain_prompt")[6] == "50"
    settings = "\n".join(_section(report, "Settings"))
    assert "| v2 | gemini-test | v | fp | temperature=0.0 | extractor-new (high) |" in settings
    assert (
        "| page_text | gemini-test | v1-frozen | — | model defaults | extractor-old (high) |"
        in (settings)
    )
    assert "10,000 resamples, seed 0, alpha 0.05" in settings
    assert GUIDE_CHANGED not in settings
    quotes = _section(report, "Quotes")
    assert _row(quotes, "v2")[1:] == ["1", "0.0 % [0.0–0.0 %] (0/1)", "0", "1", "1"]
    assert _row(quotes, "page_text")[3] == "1"
    assert "| 2026-11-13 00:00 UTC | 7 | 0 | 1 | 0 |" in _section(report, "Label checks")
    assert "- One labeler, the project's author." in _section(report, "Limits")


def test_the_report_holds_no_value_context_element_id_key_or_model_text(tmp_path: Path) -> None:
    report = build_report(_files(tmp_path))
    for e in ALL_ELEMENTS:
        for hidden in (e.value, e.context, e.element_id, sheet_key(e.element_id)):
            assert hidden not in report
    assert "secret" not in report and "relu" not in report


def test_missing_files_are_said_not_raised(tmp_path: Path) -> None:
    paths = replace(_paths(tmp_path, ITEMS), labeling_guide=tmp_path / "no-guide.md")
    report = build_report(paths)
    assert "No label sheet exported yet." in report
    assert "The labeling guide file is missing." in report
    assert "No label check run yet." in report
    assert "No comparison label imported yet." in report
    assert "page_text: no article with an output yet." in report
    assert "No conclusion is given until every label is in." in report


def test_an_output_not_extracted_keeps_the_arm_incomplete(tmp_path: Path) -> None:
    paths = _files(tmp_path)
    write_jsonl(paths.extractions, [_extraction("tsa-00:v2:article:0", "extractor-new")])
    data = load_report_data(paths)
    v2 = data.arm("v2")
    assert (v2.unextracted, v2.complete) == (1, False)
    report = render_report(data)
    assert "v2: 1 articles with an output have no successful extraction yet" in report
    assert "No conclusion is given until every label is in." in report


def test_labels_pending_in_the_files_give_no_conclusion(tmp_path: Path) -> None:
    report = build_report(_files(tmp_path, compare_labels=False))
    assert _row(_section(report, "Status"), "page_text")[5:] == ["2", "2"]
    assert _row(_section(report, "Results per arm"), "page_text")[1:5] == ["labels pending"] * 4
    assert "No conclusion is given until every label is in." in report
    differences = "\n".join(_section(report, "Differences of the unsupported rate"))
    assert differences.count("labels pending") == 9 and "points" not in differences


def test_the_report_step_writes_and_prints_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _files(tmp_path)
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    compare_main.main(["report"])
    out = capsys.readouterr().out
    assert paths.report == paths.dataset_dir / "report.md"
    written = paths.report.read_text(encoding="utf-8")
    assert out == written + "\n"
    assert written == build_report(paths)
