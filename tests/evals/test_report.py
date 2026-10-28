import math
from datetime import UTC, datetime

from evals.baseline_v1.report import build_report, cluster_bootstrap_ci, group_stats
from evals.baseline_v1.schemas import (
    ElementRecord,
    ExtractionRecord,
    HumanLabel,
    RunRecord,
    SampleItem,
)


def test_bootstrap_ci_brackets_the_point_estimate() -> None:
    clusters = [(1, 4), (0, 5), (3, 3), (2, 6), (0, 2)]
    ci = cluster_bootstrap_ci(clusters, seed=42)
    assert ci.rate == 6 / 20
    assert ci.low <= ci.rate <= ci.high
    assert cluster_bootstrap_ci(clusters, seed=42) == ci


def test_bootstrap_ci_degenerate_cases() -> None:
    assert cluster_bootstrap_ci([(0, 3), (0, 4)]).high == 0.0
    assert math.isnan(cluster_bootstrap_ci([]).rate)


def _run(run_id: str, item_id: str, text: str = "Texte") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        item_id=item_id,
        condition="url_only",
        task="summary",
        repeat=0,
        model="m",
        prompt_version="v1-frozen",
        created_at=datetime.now(UTC),
        output_text=text,
    )


def _element(element_id: str, run_id: str, item_id: str, status: str) -> ElementRecord:
    return ElementRecord(
        element_id=element_id,
        run_id=run_id,
        item_id=item_id,
        condition="url_only",
        task="summary",
        type="number",
        value="12",
        context="12",
        match_status=status,
    )


def test_group_stats_combines_labels_matches_and_pending() -> None:
    runs = [_run("r1", "a"), _run("r2", "b"), _run("r3", "b", "[TEXTE_INSUFFISANT]")]
    elements = [
        _element("e1", "r1", "a", "found"),  # assumed supported
        _element("e2", "r1", "a", "not_found"),  # labeled NOT_IN_SOURCE
        _element("e3", "r2", "b", "not_found"),  # pending
        _element("e4", "r2", "b", "found"),  # audited: CONTRADICTED
    ]
    labels = {
        "e2": HumanLabel(element_id="e2", label="NOT_IN_SOURCE"),
        "e4": HumanLabel(element_id="e4", label="CONTRADICTED"),
    }
    stats = group_stats("g", runs, elements, labels)

    assert (stats.runs, stats.refusals, stats.elements, stats.pending) == (3, 1, 4, 1)
    # e3 is pending: the element rate is bounded between 2/4 and 3/4.
    assert (stats.element_rate.low.numerator, stats.element_rate.low.denominator) == (2, 4)
    assert (stats.element_rate.high.numerator, stats.element_rate.high.denominator) == (3, 4)
    assert (stats.contradicted_rate.numerator, stats.contradicted_rate.denominator) == (1, 3)
    # r2 has a pending element but is already decided by its contradicted one.
    assert stats.undetermined_runs == 0
    assert stats.output_rate.is_point
    assert (stats.output_rate.low.numerator, stats.output_rate.low.denominator) == (2, 3)


def test_group_stats_bounds_the_output_rate_over_undetermined_runs() -> None:
    runs = [_run("r1", "a"), _run("r2", "b"), _run("r3", "c")]
    elements = [
        _element("e1", "r1", "a", "not_found"),  # labeled NOT_IN_SOURCE
        _element("e2", "r2", "b", "not_found"),  # pending
        _element("e3", "r2", "b", "found"),  # assumed supported
        _element("e4", "r3", "c", "found"),  # assumed supported
    ]
    labels = {"e1": HumanLabel(element_id="e1", label="NOT_IN_SOURCE")}
    stats = group_stats("g", runs, elements, labels)

    assert stats.undetermined_runs == 1
    assert not stats.output_rate.is_point
    assert (stats.output_rate.low.numerator, stats.output_rate.low.denominator) == (1, 3)
    assert (stats.output_rate.high.numerator, stats.output_rate.high.denominator) == (2, 3)
    assert stats.output_rate.low.rate <= stats.output_rate.high.rate
    assert stats.output_rate.low.low <= stats.output_rate.high.high


def test_group_stats_bounds_meet_once_everything_is_labeled() -> None:
    runs = [_run("r1", "a"), _run("r2", "b")]
    elements = [_element("e1", "r1", "a", "not_found"), _element("e2", "r2", "b", "not_found")]
    labels = {
        "e1": HumanLabel(element_id="e1", label="SUPPORTED"),
        "e2": HumanLabel(element_id="e2", label="CONTRADICTED"),
    }
    stats = group_stats("g", runs, elements, labels)

    assert (stats.pending, stats.undetermined_runs) == (0, 0)
    assert stats.element_rate.is_point and stats.output_rate.is_point
    assert stats.element_rate.low == stats.element_rate.high
    assert stats.output_rate.low == stats.output_rate.high


def test_build_report_renders_tables() -> None:
    items = [
        SampleItem(item_id="a", source="tsa", url="u", title="t", snapshot_id="a"),
        SampleItem(item_id="a-opaque", source="tsa", url="u?p=1", title="t", snapshot_id="a",
                   is_opaque=True),
    ]  # fmt: skip
    runs = [_run("r1", "a"), _run("r2", "a-opaque")]
    extractions = [
        ExtractionRecord(
            run_id=r, model="x", prompt_version="extract-v1", created_at=datetime.now(UTC)
        )
        for r in ("r1", "r2")
    ]
    elements = [_element("e1", "r1", "a", "found"), _element("e2", "r2", "a-opaque", "found")]

    report = build_report(items, runs, extractions, elements, {})
    assert "| url_only / summary | 1 | 0 | 1 | 0 | 0 | 0% [0%–0%] (0/1)" in report
    assert "| opaque ?p=ID | 1 |" in report
    assert "Matcher audit" in report
    assert "Provisional report" not in report


def test_build_report_flags_pending_elements_and_shows_bounds() -> None:
    items = [SampleItem(item_id="a", source="tsa", url="u", title="t", snapshot_id="a")]
    runs = [_run("r1", "a"), _run("r2", "a")]
    extractions = [
        ExtractionRecord(
            run_id=r, model="x", prompt_version="extract-v1", created_at=datetime.now(UTC)
        )
        for r in ("r1", "r2")
    ]
    elements = [_element("e1", "r1", "a", "found"), _element("e2", "r2", "a", "not_found")]

    report = build_report(items, runs, extractions, elements, {})
    assert "**Provisional report:** 1 elements are still pending" in report
    # One cluster: the bootstrap CI collapses onto each bound.
    assert "| url_only / summary | 2 | 0 | 2 | 1 | 1 | 0%–50% [0%–50%] (0–1/2)" in report
    assert report.rstrip().count("| 0%–50% [0%–50%] (0–1/2) |") == 2
    assert "| url_only | number | 1 | 0% [0%–0%] (0/1) |" in report
