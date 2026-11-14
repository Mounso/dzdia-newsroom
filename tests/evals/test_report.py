import math
from datetime import UTC, datetime

from evals.baseline_v1.report import build_report, cluster_bootstrap_ci, group_stats
from evals.baseline_v1.schemas import (
    Condition,
    ElementRecord,
    ElementType,
    ExtractionRecord,
    HumanLabel,
    Label,
    MatchStatus,
    RunRecord,
    SampleItem,
    Task,
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


def _run(
    run_id: str,
    item_id: str,
    text: str = "Texte",
    *,
    condition: Condition = "url_only",
    task: Task = "summary",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        item_id=item_id,
        condition=condition,
        task=task,
        repeat=0,
        model="m",
        prompt_version="v1-frozen",
        created_at=datetime.now(UTC),
        output_text=text,
    )


def _element(
    element_id: str,
    run_id: str,
    item_id: str,
    status: MatchStatus,
    *,
    condition: Condition = "url_only",
    task: Task = "summary",
    element_type: ElementType = "number",
) -> ElementRecord:
    return ElementRecord(
        element_id=element_id,
        run_id=run_id,
        item_id=item_id,
        condition=condition,
        task=task,
        type=element_type,
        value="12",
        context="12",
        match_status=status,
    )


def _extractions(runs: list[RunRecord]) -> list[ExtractionRecord]:
    return [
        ExtractionRecord(
            run_id=r.run_id, model="x", prompt_version="extract-v1", created_at=datetime.now(UTC)
        )
        for r in runs
    ]


def _labels(**by_id: Label) -> dict[str, HumanLabel]:
    return {k: HumanLabel(element_id=k, label=v) for k, v in by_id.items()}


def _item(item_id: str, snapshot_id: str | None = None) -> SampleItem:
    """An original item, or the opaque duplicate of `snapshot_id`."""
    return SampleItem(
        item_id=item_id,
        source="tsa",
        url=f"u/{item_id}" if snapshot_id is None else f"u?p={item_id}",
        title="t",
        snapshot_id=snapshot_id or item_id,
        is_opaque=snapshot_id is not None,
    )


def _section(report: str, heading: str | None) -> str:
    """The text under `## heading` up to the next section; `None`: the text before any section."""
    parts = report.split("\n## ")
    if heading is None:
        return parts[0]
    [section] = [p for p in parts if p.startswith(heading)]
    return section


def _row(section: str, *keys: str) -> list[str]:
    """The cells of the one table row of `section` whose first cells are `keys`."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in section.splitlines()
        if line.startswith("| ")
    ]
    [row] = [r for r in rows if r[: len(keys)] == list(keys)]
    return row


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
    assert stats.unlabeled_found == 1  # e1
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

    assert (stats.pending, stats.undetermined_runs, stats.unlabeled_found) == (0, 0, 0)
    assert stats.element_rate.is_point and stats.output_rate.is_point
    assert stats.element_rate.low == stats.element_rate.high
    assert stats.output_rate.low == stats.output_rate.high


def test_build_report_renders_tables() -> None:
    items = [_item("a"), _item("a-opaque", "a")]
    runs = [_run("r1", "a"), _run("r2", "a-opaque")]
    elements = [_element("e1", "r1", "a", "found"), _element("e2", "r2", "a-opaque", "found")]

    report = build_report(items, runs, _extractions(runs), elements, {})
    assert "protocol: ADR-0001, reported per ADR-0016" in report
    # e1 is an unlabeled 'found' element: every rate of the row is a floor.
    assert (
        "| url_only / summary | 1 | 0 | 1 | 0 | 1 | 0 | ≥ 0% (0/1), CI from 0% "
        "| ≥ 0% (0/1), CI from 0% | ≥ 0% (0/1), CI from 0% |"
    ) in report
    # The opaque item is out of the main table and in the slug section.
    assert "opaque ?p=ID" not in report and "slug (originals)" not in report
    assert _row(_section(report, "Slug effect"), "a") == ["a", "0/1", "1", "0/1", "1", "no"]
    assert "Matcher audit" in report
    assert "Provisional report" not in report


def test_build_report_adds_a_row_per_condition_over_original_items() -> None:
    items = [_item("a"), _item("a-opaque", "a")]
    article = _run("r2", "a", task="article")
    runs = [_run("r1", "a"), article, _run("r3", "a-opaque")]
    elements = [
        _element("e1", "r1", "a", "not_found"),
        _element("e2", "r2", "a", "found", task="article"),
        _element("e3", "r2", "a", "found", task="article"),
        _element("e4", "r3", "a-opaque", "not_found"),  # opaque: excluded from the row
    ]
    labels = _labels(e1="NOT_IN_SOURCE", e2="CONTRADICTED", e4="NOT_IN_SOURCE")

    report = build_report(items, runs, _extractions(runs), elements, labels)
    # Both tasks of the original item: 2 outputs, 3 elements, 2 unsupported, both outputs hit;
    # e3 is unlabeled 'found', so the blend row gives floors.
    assert (
        "| url_only / all tasks (blend) | 2 | 0 | 3 | 0 | 1 | 0 | ≥ 67% (2/3), CI from 67% "
    ) in report
    assert "| ≥ 100% (2/2), CI from 100% |" in report
    # Audited 'found' of the original item, per condition and task: e2 only, CONTRADICTED.
    audit = _section(report, "Matcher audit")
    assert _row(audit, "url_only / article") == ["url_only / article", "2", "1", "0% [0%–0%] (0/1)"]
    assert _row(audit, "url_only / summary") == ["url_only / summary", "0", "0", "n/a"]
    assert _row(audit, "page_text / article") == ["page_text / article", "0", "0", "n/a"]
    assert _row(audit, "page_text / summary") == ["page_text / summary", "0", "0", "n/a"]


def test_build_report_flags_pending_elements_and_shows_bounds() -> None:
    items = [_item("a")]
    runs = [_run("r1", "a"), _run("r2", "a")]
    elements = [_element("e1", "r1", "a", "found"), _element("e2", "r2", "a", "not_found")]
    # e1 is labeled: no unlabeled 'found' element, so the pending bounds are printed.
    labels = _labels(e1="SUPPORTED")

    report = build_report(items, runs, _extractions(runs), elements, labels)
    assert "**Provisional report:** 1 elements are still pending" in report
    # One cluster: the bootstrap CI collapses onto each bound.
    assert "| url_only / summary | 2 | 0 | 2 | 1 | 0 | 1 | 0%–50% [0%–50%] (0–1/2)" in report
    # Element and output bounds, on the summary row and on the identical blend row.
    assert report.rstrip().count("| 0%–50% [0%–50%] (0–1/2) |") == 4
    by_type = _section(report, "Unsupported rate by element type")
    assert _row(by_type, "url_only / summary", "number") == [
        "url_only / summary", "number", "1", "0", "0% [0%–0%] (0/1)",
    ]  # fmt: skip


# --- ADR-0016, points 1, 3 and 6 ------------------------------------------------------------


def _one_cell(labels: dict[str, HumanLabel]) -> str:
    """`url_only / article` over two articles: e1 found, e2 not_found, e3 found (item b)."""
    items = [_item("a"), _item("b")]
    runs = [_run("r1", "a", task="article"), _run("r2", "b", task="article")]
    elements = [
        _element("e1", "r1", "a", "found", task="article"),
        _element("e2", "r1", "a", "not_found", task="article"),
        _element("e3", "r2", "b", "found", task="article"),
    ]
    return build_report(items, runs, _extractions(runs), elements, labels)


_RATE_COLUMNS = slice(7, 10)  # unsupported elements, contradicted, outputs with >=1 unsupported


def test_a_fully_labeled_cell_prints_rates_with_their_interval() -> None:
    report = _one_cell(_labels(e1="SUPPORTED", e2="NOT_IN_SOURCE", e3="CONTRADICTED"))
    row = _row(_section(report, None), "url_only / article")
    assert row[1:7] == ["2", "0", "3", "0", "0", "0"]
    assert row[_RATE_COLUMNS] == [
        "67% [50%–100%] (2/3)",
        "33% [0%–100%] (1/3)",
        "100% [100%–100%] (2/2)",
    ]
    assert not any("≥" in cell for cell in row)


def test_a_cell_with_an_unlabeled_found_element_prints_floors() -> None:
    report = _one_cell(_labels(e2="NOT_IN_SOURCE", e3="CONTRADICTED"))  # e1 not labeled
    row = _row(_section(report, None), "url_only / article")
    assert row[5] == "1"  # Unlabeled found
    assert row[_RATE_COLUMNS] == [
        "≥ 67% (2/3), CI from 50%",
        "≥ 33% (1/3), CI from 0%",
        "≥ 100% (2/2), CI from 100%",
    ]
    for cell in row[_RATE_COLUMNS]:
        assert "–" not in cell and "[" not in cell and "]" not in cell
    # The blend row holds e1 too; the empty summary row has no rate.
    blend = _row(_section(report, None), "url_only / all tasks (blend)")
    assert blend[5] == "1" and blend[7].startswith("≥ 67% (2/3)")
    assert _row(_section(report, None), "url_only / summary")[_RATE_COLUMNS] == ["n/a"] * 3


def test_pending_and_unlabeled_found_elements_together_still_give_floors() -> None:
    items = [_item("a")]
    runs = [_run("r1", "a"), _run("r2", "a")]
    elements = [_element("e1", "r1", "a", "found"), _element("e2", "r2", "a", "not_found")]

    report = build_report(items, runs, _extractions(runs), elements, {})
    assert "**Provisional report:** 1 elements are still pending" in report
    row = _row(_section(report, None), "url_only / summary")
    assert row[1:7] == ["2", "0", "2", "1", "1", "1"]
    # The floor is the low bound (pending counted as supported), with no range and no upper end.
    assert row[_RATE_COLUMNS] == [
        "≥ 0% (0/2), CI from 0%",
        "≥ 0% (0/1), CI from 0%",
        "≥ 0% (0/2), CI from 0%",
    ]
    assert "0%–50%" not in report


def test_the_blend_rows_are_named_and_explained() -> None:
    report = _one_cell({})
    preamble = _section(report, None)
    labels = [line.split("|")[1].strip() for line in preamble.splitlines() if line.startswith("| ")]
    assert labels[1:] == [
        "url_only / summary",
        "url_only / article",
        "url_only / all tasks (blend)",
        "page_text / summary",
        "page_text / article",
        "page_text / all tasks (blend)",
    ]
    assert "| Pending | Unlabeled found | Undetermined outputs |" in preamble
    assert "`all tasks (blend)` rows pool the summary and article outputs" in preamble
    assert "not a result to quote" in preamble
    # The floor format is explained: unlabeled 'found' elements count as supported.
    assert "`≥ X% (k/n), CI from L%`" in preamble and "no upper end" in preamble


def test_the_by_type_table_is_per_task_and_applies_the_floor_rule() -> None:
    items = [_item("a"), _item("a-opaque", "a")]
    runs = [
        _run("r1", "a", task="article"),
        _run("r2", "a"),
        _run("r3", "a-opaque", task="article"),
    ]
    elements = [
        # url_only / article, person: fully labeled.
        _element("p1", "r1", "a", "found", task="article", element_type="person"),
        _element("p2", "r1", "a", "not_found", task="article", element_type="person"),
        # url_only / article, number: one unlabeled 'found' element.
        _element("n1", "r1", "a", "found", task="article"),
        _element("n2", "r1", "a", "not_found", task="article"),
        # url_only / summary, person: one pending element, nothing else.
        _element("s1", "r2", "a", "not_found", element_type="person"),
        # Opaque: not in the table.
        _element("o1", "r3", "a-opaque", "found", task="article", element_type="person"),
    ]
    labels = _labels(p1="SUPPORTED", p2="CONTRADICTED", n2="NOT_IN_SOURCE")

    report = build_report(items, runs, _extractions(runs), elements, labels)
    by_type = _section(report, "Unsupported rate by element type (original items)")
    assert "| Condition / task | Type | Pending | Unlabeled found | Unsupported |" in by_type
    assert sum(line.startswith("| url_only / ") for line in by_type.splitlines()) == 12
    assert sum(line.startswith("| page_text / ") for line in by_type.splitlines()) == 12
    assert _row(by_type, "url_only / article", "person") == [
        "url_only / article", "person", "0", "0", "50% [50%–50%] (1/2)",
    ]  # fmt: skip
    assert _row(by_type, "url_only / article", "number") == [
        "url_only / article", "number", "0", "1", "≥ 50% (1/2), CI from 50%",
    ]  # fmt: skip
    assert _row(by_type, "url_only / summary", "person") == [
        "url_only / summary", "person", "1", "0", "n/a",
    ]  # fmt: skip


def test_the_matcher_audit_is_given_per_condition_and_task() -> None:
    items = [_item("a")]
    runs = [_run("r1", "a"), _run("r2", "a", task="article")]
    elements = [
        _element("s1", "r1", "a", "found"),
        _element("a1", "r2", "a", "found", task="article"),
        _element("a2", "r2", "a", "found", task="article"),
        _element("a3", "r2", "a", "not_found", task="article"),  # not a 'found' element
    ]
    labels = _labels(s1="SUPPORTED", a1="CONTRADICTED", a3="NOT_IN_SOURCE")

    report = build_report(items, runs, _extractions(runs), elements, labels)
    audit = _section(report, "Matcher audit")
    assert "Precision of automatic" not in audit and "- url_only:" not in audit
    assert "| Condition / task | found elements | Labeled | Confirmed supported |" in audit
    assert "count as supported" in audit and "full count, not a sample" in audit
    assert _row(audit, "url_only / summary") == [
        "url_only / summary", "1", "1", "100% [100%–100%] (1/1)",
    ]  # fmt: skip
    assert _row(audit, "url_only / article") == ["url_only / article", "2", "1", "0% [0%–0%] (0/1)"]


def test_the_slug_effect_is_given_in_counts_per_article() -> None:
    items = [
        _item("a"), _item("a-op", "a"),
        _item("b"), _item("b-op", "b"),
        _item("c"), _item("c-op", "c"),
        _item("d"),  # no opaque duplicate: not in the section
    ]  # fmt: skip
    runs = [
        _run("ra", "a"), _run("ra2", "a", task="article"), _run("rao", "a-op"),
        _run("rb", "b"), _run("rbo", "b-op"),
        _run("rc", "c"), _run("rco", "c-op"),
        _run("rd", "d"),
        _run("rap", "a", condition="page_text"),  # page_text: not in the section
    ]  # fmt: skip
    elements = [
        _element("a1", "ra", "a", "not_found"),  # NOT_IN_SOURCE
        _element("a2", "ra", "a", "found"),  # unlabeled
        _element("a3", "ra2", "a", "found", task="article"),  # SUPPORTED
        _element("o1", "rao", "a-op", "not_found"),  # NOT_IN_SOURCE
        _element("o2", "rao", "a-op", "found"),  # CONTRADICTED
        _element("o3", "rao", "a-op", "not_found"),  # pending
        _element("b1", "rb", "b", "found"),  # CONTRADICTED
        _element("b2", "rb", "b", "found"),  # unlabeled
        _element("p1", "rbo", "b-op", "found"),  # unlabeled
        _element("p2", "rbo", "b-op", "not_found"),  # NOT_IN_SOURCE
        _element("q1", "rco", "c-op", "not_found"),  # NOT_IN_SOURCE; c has no element
        _element("d1", "rd", "d", "not_found"),  # NOT_IN_SOURCE
        _element("x1", "rap", "a", "not_found", condition="page_text"),  # NOT_IN_SOURCE
    ]
    labels = _labels(
        a1="NOT_IN_SOURCE", a3="SUPPORTED", o1="NOT_IN_SOURCE", o2="CONTRADICTED",
        b1="CONTRADICTED", p2="NOT_IN_SOURCE", q1="NOT_IN_SOURCE", d1="NOT_IN_SOURCE",
        x1="NOT_IN_SOURCE",
    )  # fmt: skip

    report = build_report(items, runs, _extractions(runs), elements, labels)
    slug = _section(report, "Slug effect")
    assert (
        "| Article | Normal URL: unsupported / elements | Normal URL: unlabeled "
        "| Opaque URL: unsupported / elements | Opaque URL: unlabeled | Share rises |"
    ) in slug
    assert _row(slug, "a") == ["a", "1/3", "1", "2/3", "1", "yes"]
    assert _row(slug, "b") == ["b", "1/2", "1", "1/2", "1", "no"]
    assert _row(slug, "c") == ["c", "0/0", "0", "1/1", "0", "n/a"]
    assert _row(slug, "All") == ["All", "2/5", "2", "4/6", "2", "—"]
    assert not any(line.startswith("| d |") for line in slug.splitlines())
    assert "rises in 1 of the 2 articles" in slug
    assert "floors" in slug and "too few" in slug
    assert "[" not in slug and "%" not in slug
    # The slug rows are gone from the main table.
    assert "slug (originals)" not in report and "opaque ?p=ID" not in report


def test_the_slug_section_renders_without_an_opaque_item() -> None:
    report = _one_cell({})
    slug = _section(report, "Slug effect")
    assert "no opaque duplicate" in slug
    assert "| Article |" not in slug
