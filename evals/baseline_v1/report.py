"""Aggregate labels into hallucination rates with cluster-bootstrap confidence intervals.

Final verdict per element: the human label if any; otherwise "found" elements are assumed
SUPPORTED (the audit sample measures how wrong that assumption is) and "not_found" ones are
pending.

Pending elements are not missing at random: they are all "not_found", the elements most likely
to be unsupported, so dropping them would bias the rates low. The element and output rates are
therefore worst-case (Manski) bounds: pending counted as supported (low), then as unsupported
(high). The bounds meet once every pending element is labeled. The contradicted rate and the
per-type rates are over decided elements only; the report shows the pending counts next to them.

Reporting rule (ADR-0016, point 3): a population is printed as a rate with its 95 % CI only when
it has no unlabeled "found" element. Otherwise those elements are counted as supported, so the
figure is a floor: `≥ X% (k/n), CI from L%`, built from the low bound, with no upper end. The
report is given per condition and task (the "all tasks (blend)" rows pool both tasks), the matcher
audit per condition and task, and the slug effect in per-article counts with no interval
(point 6).
"""

import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from evals.baseline_v1.schemas import (
    ElementRecord,
    ExtractionRecord,
    HumanLabel,
    RunRecord,
    SampleItem,
)
from evals.baseline_v1.v1_prompts import INSUFFICIENT_TEXT_MARKER

Verdict = Literal["supported", "contradicted", "not_in_source", "pending"]
_UNSUPPORTED: frozenset[Verdict] = frozenset({"contradicted", "not_in_source"})


@dataclass(frozen=True)
class RateCI:
    rate: float
    low: float
    high: float
    numerator: int
    denominator: int


def cluster_bootstrap_ci(
    clusters: Sequence[tuple[int, int]],
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> RateCI:
    """Ratio of sums with a percentile CI, resampling whole clusters (items), not elements."""
    numerator = sum(n for n, _ in clusters)
    denominator = sum(d for _, d in clusters)
    if denominator == 0:
        nan = float("nan")
        return RateCI(nan, nan, nan, 0, 0)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(n_boot):
        resample = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        den = sum(d for _, d in resample)
        if den:
            estimates.append(sum(n for n, _ in resample) / den)
    estimates.sort()
    low = estimates[int(alpha / 2 * (len(estimates) - 1))]
    high = estimates[int((1 - alpha / 2) * (len(estimates) - 1))]
    return RateCI(numerator / denominator, low, high, numerator, denominator)


def verdict(element: ElementRecord, labels: Mapping[str, HumanLabel]) -> Verdict:
    if element.element_id in labels:
        return {
            "SUPPORTED": "supported",
            "CONTRADICTED": "contradicted",
            "NOT_IN_SOURCE": "not_in_source",
        }[labels[element.element_id].label]  # type: ignore[return-value]
    return "supported" if element.match_status == "found" else "pending"


def _rate_by_item(pairs: Iterable[tuple[str, bool]]) -> RateCI:
    """pairs: (item_id, is_unsupported) -> clustered rate of unsupported."""
    per_item: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item_id, unsupported in pairs:
        per_item[item_id][0] += int(unsupported)
        per_item[item_id][1] += 1
    return cluster_bootstrap_ci([(n, d) for n, d in per_item.values()])


@dataclass(frozen=True)
class RateBounds:
    """Worst-case bounds: pending counted as supported (low) or as unsupported (high)."""

    low: RateCI
    high: RateCI

    @property
    def is_point(self) -> bool:
        return self.low.numerator == self.high.numerator


def _rate_bounds(triples: Iterable[tuple[str, bool, bool]]) -> RateBounds:
    """triples: (item_id, is_unsupported, is_pending) -> bounds on the clustered rate.

    Both bounds use the same items in the same order and the same seed, so their bootstrap
    resamples are paired.
    """
    rows = list(triples)
    return RateBounds(
        low=_rate_by_item((item_id, unsupported) for item_id, unsupported, _ in rows),
        high=_rate_by_item(
            (item_id, unsupported or pending) for item_id, unsupported, pending in rows
        ),
    )


@dataclass(frozen=True)
class GroupStats:
    label: str
    runs: int
    refusals: int
    elements: int
    pending: int
    unlabeled_found: int
    """'found' elements with no human label: while there is one, every rate is a floor."""
    undetermined_runs: int
    """Runs with pending elements and no unsupported one yet: their verdict is still open."""
    element_rate: RateBounds
    contradicted_rate: RateCI
    output_rate: RateBounds


def group_stats(
    label: str,
    runs: Sequence[RunRecord],
    elements: Sequence[ElementRecord],
    labels: Mapping[str, HumanLabel],
) -> GroupStats:
    verdicts = {e.element_id: verdict(e, labels) for e in elements}
    decided = [e for e in elements if verdicts[e.element_id] != "pending"]
    unsupported_runs = {e.run_id for e in decided if verdicts[e.element_id] in _UNSUPPORTED}
    pending_runs = {e.run_id for e in elements if verdicts[e.element_id] == "pending"}
    # A run with one unsupported element is decided whatever its pending elements turn out to be.
    undetermined_runs = {r.run_id for r in runs} & (pending_runs - unsupported_runs)
    return GroupStats(
        label=label,
        runs=len(runs),
        refusals=sum(INSUFFICIENT_TEXT_MARKER in (r.output_text or "") for r in runs),
        elements=len(elements),
        pending=len(elements) - len(decided),
        unlabeled_found=count_unlabeled_found(elements, labels),
        undetermined_runs=len(undetermined_runs),
        element_rate=_rate_bounds(
            (
                e.item_id,
                verdicts[e.element_id] in _UNSUPPORTED,
                verdicts[e.element_id] == "pending",
            )
            for e in elements
        ),
        contradicted_rate=_rate_by_item(
            (e.item_id, verdicts[e.element_id] == "contradicted") for e in decided
        ),
        output_rate=_rate_bounds(
            (r.item_id, r.run_id in unsupported_runs, r.run_id in undetermined_runs) for r in runs
        ),
    )


def count_unlabeled_found(
    elements: Iterable[ElementRecord], labels: Mapping[str, HumanLabel]
) -> int:
    """'found' elements with no human label: they count as supported, so rates are floors."""
    return sum(e.match_status == "found" and e.element_id not in labels for e in elements)


def matcher_precision(
    elements: Iterable[ElementRecord], labels: Mapping[str, HumanLabel]
) -> RateCI:
    """Share of audited 'found' elements that humans confirmed as SUPPORTED."""
    audited = [e for e in elements if e.match_status == "found" and e.element_id in labels]
    pairs = ((e.item_id, labels[e.element_id].label == "SUPPORTED") for e in audited)
    return _rate_by_item(pairs)


def _fmt(ci: RateCI) -> str:
    if ci.denominator == 0:
        return "n/a"
    return f"{ci.rate:.0%} [{ci.low:.0%}–{ci.high:.0%}] ({ci.numerator}/{ci.denominator})"


def _fmt_bounds(bounds: RateBounds) -> str:
    """One estimate once nothing is pending; otherwise both bounds and the outer CI ends."""
    low, high = bounds.low, bounds.high
    if low.denominator == 0 or bounds.is_point:
        return _fmt(low)
    return (
        f"{low.rate:.0%}–{high.rate:.0%} [{low.low:.0%}–{high.high:.0%}] "
        f"({low.numerator}–{high.numerator}/{low.denominator})"
    )


def _fmt_floor(ci: RateCI) -> str:
    """A rate that can only go up: the point estimate and the lower end of its CI only."""
    if ci.denominator == 0:
        return "n/a"
    return f"≥ {ci.rate:.0%} ({ci.numerator}/{ci.denominator}), CI from {ci.low:.0%}"


def _fmt_rate(value: RateCI | RateBounds, unlabeled_found: int) -> str:
    """Every rate cell of the report (ADR-0016, point 3): a rate while the population has no
    unlabeled 'found' element, otherwise a floor built from the low bound."""
    if unlabeled_found:
        return _fmt_floor(value.low if isinstance(value, RateBounds) else value)
    return _fmt_bounds(value) if isinstance(value, RateBounds) else _fmt(value)


_CONDITIONS = ("url_only", "page_text")
_TASKS = ("summary", "article")
_ELEMENT_TYPES = ("person", "organization", "place", "number", "date", "quote")


@dataclass(frozen=True)
class SlugSide:
    """Counts of one URL kind for one article: floors, since no label means not unsupported."""

    unsupported: int
    elements: int
    unlabeled: int


def _slug_side(elements: Sequence[ElementRecord], labels: Mapping[str, HumanLabel]) -> SlugSide:
    return SlugSide(
        unsupported=sum(verdict(e, labels) in _UNSUPPORTED for e in elements),
        elements=len(elements),
        unlabeled=sum(e.element_id not in labels for e in elements),
    )


def share_rises(normal: SlugSide, opaque: SlugSide) -> bool | None:
    """Whether the opaque URL's unsupported share is higher; None when a side has no element."""
    if normal.elements == 0 or opaque.elements == 0:
        return None
    # Integer cross-multiplication: no float comparison of two ratios.
    return opaque.unsupported * normal.elements > normal.unsupported * opaque.elements


def _slug_section(
    items: Sequence[SampleItem],
    ok_runs: Sequence[RunRecord],
    elements: Sequence[ElementRecord],
    labels: Mapping[str, HumanLabel],
) -> list[str]:
    lines = ["## Slug effect", ""]
    opaque_by_original: dict[str, set[str]] = defaultdict(set)
    for item in items:
        if item.is_opaque:
            opaque_by_original[item.snapshot_id].add(item.item_id)
    if not opaque_by_original:
        lines += ["The sample has no opaque duplicate: there is no slug effect to report.", ""]
        return lines

    def side(item_ids: set[str]) -> list[ElementRecord]:
        run_ids = {r.run_id for r in ok_runs if r.condition == "url_only" and r.item_id in item_ids}
        return [e for e in elements if e.run_id in run_ids]

    lines += [
        "Original articles also reached through an opaque `?p=ID` URL, `url_only` outputs of all "
        "tasks. Counts only.",
        "",
        "| Article | Normal URL: unsupported / elements | Normal URL: unlabeled "
        "| Opaque URL: unsupported / elements | Opaque URL: unlabeled | Share rises |",
        "|---|---|---|---|---|---|",
    ]
    all_normal: list[ElementRecord] = []
    all_opaque: list[ElementRecord] = []
    compared = rises = 0
    for original in sorted(opaque_by_original):
        normal_elements = side({original})
        opaque_elements = side(opaque_by_original[original])
        all_normal += normal_elements
        all_opaque += opaque_elements
        normal, opaque = _slug_side(normal_elements, labels), _slug_side(opaque_elements, labels)
        rise = share_rises(normal, opaque)
        compared += rise is not None
        rises += rise is True
        cell = "n/a" if rise is None else ("yes" if rise else "no")
        lines.append(f"| {original} | {_slug_cells(normal)} | {_slug_cells(opaque)} | {cell} |")
    pooled_normal, pooled_opaque = _slug_side(all_normal, labels), _slug_side(all_opaque, labels)
    lines += [
        f"| All | {_slug_cells(pooled_normal)} | {_slug_cells(pooled_opaque)} | — |",
        "",
        f"The share of unsupported elements rises in {rises} of the {compared} articles where "
        "both URLs gave elements. These counts are floors: an element with no label counts as "
        "supported when `found`, and a pending `not_found` one is not counted as unsupported "
        f"either. No interval is printed: {compared} articles are too few to quantify the effect.",
        "",
    ]
    return lines


def _slug_cells(side: SlugSide) -> str:
    return f"{side.unsupported}/{side.elements} | {side.unlabeled}"


def build_report(
    items: Sequence[SampleItem],
    runs: Sequence[RunRecord],
    extractions: Sequence[ExtractionRecord],
    elements: Sequence[ElementRecord],
    labels: Mapping[str, HumanLabel],
) -> str:
    opaque_ids = {i.item_id for i in items if i.is_opaque}
    extracted = {x.run_id for x in extractions if x.error is None}
    ok_runs = [r for r in runs if r.error is None and r.run_id in extracted]
    models = sorted({r.model for r in runs})

    groups: list[GroupStats] = []
    for condition in _CONDITIONS:
        # One row per task, then the blend of both tasks, which is not a result to quote.
        for task, tasks in (("summary", {"summary"}), ("article", {"article"}),
                            ("all tasks (blend)", {"summary", "article"})):  # fmt: skip
            g_runs = [
                r
                for r in ok_runs
                if r.condition == condition and r.task in tasks and r.item_id not in opaque_ids
            ]
            run_ids = {r.run_id for r in g_runs}
            g_elements = [e for e in elements if e.run_id in run_ids]
            groups.append(group_stats(f"{condition} / {task}", g_runs, g_elements, labels))

    ok_run_ids = {r.run_id for r in ok_runs}
    pending_total = sum(
        e.run_id in ok_run_ids and verdict(e, labels) == "pending" for e in elements
    )

    lines = [
        "# Baseline v1 — hallucination report",
        "",
        f"Models: {', '.join(models) or 'n/a'} · prompts: v1-frozen · "
        "protocol: ADR-0001, reported per ADR-0016",
        "",
    ]
    if pending_total:
        lines += [
            f"> **Provisional report:** {pending_total} elements are still pending a label. "
            "Element and output rates are shown as bounds (pending counted as supported – as "
            "unsupported); the other rates cover decided elements only.",
            "",
        ]
    lines += [
        "Unsupported = CONTRADICTED + NOT_IN_SOURCE, 95 % CI from a bootstrap over articles. "
        "*Output rate* = share of outputs with at least one unsupported element. A range "
        "`low–high [CI]` bounds a rate while elements are pending; the CI spans the low bound's "
        "lower end to the high bound's upper end. *Undetermined* outputs have pending elements "
        "and no unsupported one yet. A cell written `≥ X% (k/n), CI from L%` is a floor: its "
        "population still has *unlabeled found* elements, which count as supported, so the "
        "true rate can only be higher; it has no upper end, and L is the lower end of the "
        "floor's own CI. A rate with its full CI is printed only for a population whose `found` "
        "elements are all labeled. The `all tasks (blend)` rows pool the summary and article "
        "outputs: they move with the number of summaries generated per article and are not a "
        "result to quote. Original items only; the opaque duplicates are in *Slug effect*.",
        "",
        "| Condition / task | Outputs | Refusals | Elements | Pending | Unlabeled found "
        "| Undetermined outputs | Unsupported elements | Contradicted (decided) "
        "| Outputs with ≥1 unsupported |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for g in groups:
        lines.append(
            f"| {g.label} | {g.runs} | {g.refusals} | {g.elements} | {g.pending} "
            f"| {g.unlabeled_found} | {g.undetermined_runs} "
            f"| {_fmt_rate(g.element_rate, g.unlabeled_found)} "
            f"| {_fmt_rate(g.contradicted_rate, g.unlabeled_found)} "
            f"| {_fmt_rate(g.output_rate, g.unlabeled_found)} |"
        )

    cells = {
        (condition, task): [
            e
            for e in elements
            if e.condition == condition and e.task == task and e.item_id not in opaque_ids
        ]
        for condition in _CONDITIONS
        for task in _TASKS
    }

    lines += ["", "## Unsupported rate by element type (original items)", ""]
    lines += [
        "Over decided elements, per condition and task.",
        "",
        "| Condition / task | Type | Pending | Unlabeled found | Unsupported |",
        "|---|---|---|---|---|",
    ]
    for (condition, task), cell in cells.items():
        for element_type in _ELEMENT_TYPES:
            subset = [e for e in cell if e.type == element_type]
            decided = [e for e in subset if verdict(e, labels) != "pending"]
            unlabeled = count_unlabeled_found(subset, labels)
            pairs = ((e.item_id, verdict(e, labels) in _UNSUPPORTED) for e in decided)
            lines.append(
                f"| {condition} / {task} | {element_type} | {len(subset) - len(decided)} "
                f"| {unlabeled} | {_fmt_rate(_rate_by_item(pairs), unlabeled)} |"
            )

    lines += [
        "",
        "## Matcher audit",
        "",
        "Unlabeled 'found' elements count as supported in the tables above, which is why a row "
        "with unlabeled 'found' elements gives floors. *Confirmed supported* is the share of the "
        "labeled 'found' elements of a cell that a human confirmed as SUPPORTED; when every "
        "'found' element of a cell is labeled, its line is a full count, not a sample.",
        "",
        "| Condition / task | found elements | Labeled | Confirmed supported |",
        "|---|---|---|---|",
    ]
    for (condition, task), cell in cells.items():
        found = [e for e in cell if e.match_status == "found"]
        labeled = sum(e.element_id in labels for e in found)
        lines.append(
            f"| {condition} / {task} | {len(found)} | {labeled} "
            f"| {_fmt(matcher_precision(cell, labels))} |"
        )

    lines += ["", *_slug_section(items, ok_runs, elements, labels)]
    return "\n".join(lines)
