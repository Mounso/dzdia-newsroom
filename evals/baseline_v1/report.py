"""Aggregate labels into hallucination rates with cluster-bootstrap confidence intervals.

Final verdict per element: the human label if any; otherwise "found" elements are assumed
SUPPORTED (the audit sample measures how wrong that assumption is) and "not_found" ones are
pending.

Pending elements are not missing at random: they are all "not_found", the elements most likely
to be unsupported, so dropping them would bias the rates low. The element and output rates are
therefore worst-case (Manski) bounds: pending counted as supported (low), then as unsupported
(high). The bounds meet once every pending element is labeled. The contradicted rate and the
per-type rates are over decided elements only; the report shows the pending counts next to them.
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


def build_report(
    items: Sequence[SampleItem],
    runs: Sequence[RunRecord],
    extractions: Sequence[ExtractionRecord],
    elements: Sequence[ElementRecord],
    labels: Mapping[str, HumanLabel],
) -> str:
    opaque_ids = {i.item_id for i in items if i.is_opaque}
    opaque_originals = {i.snapshot_id for i in items if i.is_opaque}
    extracted = {x.run_id for x in extractions if x.error is None}
    ok_runs = [r for r in runs if r.error is None and r.run_id in extracted]
    models = sorted({r.model for r in runs})

    groups: list[GroupStats] = []
    for condition in ("url_only", "page_text"):
        for task in ("summary", "article"):
            g_runs = [
                r
                for r in ok_runs
                if r.condition == condition and r.task == task and r.item_id not in opaque_ids
            ]
            run_ids = {r.run_id for r in g_runs}
            g_elements = [e for e in elements if e.run_id in run_ids]
            groups.append(group_stats(f"{condition} / {task}", g_runs, g_elements, labels))

    slug_groups: list[GroupStats] = []
    for name, ids in (("slug (originals)", opaque_originals), ("opaque ?p=ID", opaque_ids)):
        g_runs = [r for r in ok_runs if r.condition == "url_only" and r.item_id in ids]
        run_ids = {r.run_id for r in g_runs}
        g_elements = [e for e in elements if e.run_id in run_ids]
        slug_groups.append(group_stats(name, g_runs, g_elements, labels))

    ok_run_ids = {r.run_id for r in ok_runs}
    pending_total = sum(
        e.run_id in ok_run_ids and verdict(e, labels) == "pending" for e in elements
    )

    lines = [
        "# Baseline v1 — hallucination report",
        "",
        f"Models: {', '.join(models) or 'n/a'} · prompts: v1-frozen · protocol: ADR-001",
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
        "and no unsupported one yet.",
        "",
        "| Condition / task | Outputs | Refusals | Elements | Pending | Undetermined outputs "
        "| Unsupported elements | Contradicted (decided) | Outputs with ≥1 unsupported |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g in [*groups, *slug_groups]:
        lines.append(
            f"| {g.label} | {g.runs} | {g.refusals} | {g.elements} | {g.pending} "
            f"| {g.undetermined_runs} | {_fmt_bounds(g.element_rate)} "
            f"| {_fmt(g.contradicted_rate)} | {_fmt_bounds(g.output_rate)} |"
        )

    lines += ["", "## Unsupported rate by element type (decided elements, all conditions)", ""]
    lines += ["| Condition | Type | Pending | Unsupported |", "|---|---|---|---|"]
    for condition in ("url_only", "page_text"):
        for element_type in ("person", "organization", "place", "number", "date", "quote"):
            subset = [
                e
                for e in elements
                if e.condition == condition
                and e.type == element_type
                and e.item_id not in opaque_ids
            ]
            decided = [e for e in subset if verdict(e, labels) != "pending"]
            pairs = ((e.item_id, verdict(e, labels) in _UNSUPPORTED) for e in decided)
            lines.append(
                f"| {condition} | {element_type} | {len(subset) - len(decided)} "
                f"| {_fmt(_rate_by_item(pairs))} |"
            )

    lines += [
        "",
        "## Matcher audit",
        "",
        f"Precision of automatic 'found' (human-confirmed SUPPORTED): "
        f"{_fmt(matcher_precision(elements, labels))}",
        "",
    ]
    return "\n".join(lines)
