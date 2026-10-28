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
    assert (stats.element_rate.numerator, stats.element_rate.denominator) == (2, 3)
    assert (stats.contradicted_rate.numerator, stats.contradicted_rate.denominator) == (1, 3)
    assert (stats.output_rate.numerator, stats.output_rate.denominator) == (2, 3)


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
    assert "| url_only / summary | 1 | 0 | 1 | 0 | 0% [0%–0%] (0/1)" in report
    assert "| opaque ?p=ID | 1 |" in report
    assert "Matcher audit" in report
