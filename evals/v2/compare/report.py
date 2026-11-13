"""The `report` step of the comparison (ADR-0013, points 8 and 9; ADR-0010, points 9 to 12).

- Three arms, in this order: `page_text` (the baseline's runs, elements and labels, plus the
  comparison's labels), `plain_prompt` and `v2` (the comparison's runs, elements and labels).
- A verdict is a human label only: there is no fallback to the matcher, so an element without a
  label is pending. Unsupported = CONTRADICTED + NOT_IN_SOURCE.
- An arm with a pending label gets no rate, no per-type count, no difference and no conclusion:
  only its counts (ADR-0013, point 9). There is no interim look at partial results.
- Intervals: percentile cluster bootstrap over articles, 10,000 resamples, seed 0, with the index
  convention of the v1 report (ADR-0013, point 8).
- The report holds counts, rates and settings only: never model text, an element value, a
  context, an element id or a sheet key.

The statistics and the rendering are pure functions of `ReportData`; `load_report_data` is the
only part that reads files.
"""

import math
import random
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from dzdia_newsroom.tools.article import IssueKind
from evals.baseline_v1.jsonl import latest_by, read_jsonl
from evals.baseline_v1.report import RateCI, cluster_bootstrap_ci
from evals.baseline_v1.schemas import ElementRecord, ExtractionRecord, HumanLabel, RunRecord
from evals.v2.compare import CompareError
from evals.v2.compare.labels import CheckRecord, ExportRecord, guide_sha256, latest_labels
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.run import (
    MAX_FAILED_V2,
    CompareCondition,
    CompareRun,
    compare_run_id,
    comparison_items,
)

Arm = Literal["page_text", "plain_prompt", "v2"]
ARMS: tuple[Arm, ...] = ("page_text", "plain_prompt", "v2")

# Fixed before any output of the new arms existed (ADR-0013, point 8).
N_BOOT = 10_000
BOOT_SEED = 0
ALPHA = 0.05

_UNSUPPORTED = frozenset({"CONTRADICTED", "NOT_IN_SOURCE"})
PENDING = "labels pending"
NONE = "—"
# The differences shown, primary first (ADR-0010, points 9 and 10).
DIFFERENCES: tuple[tuple[Arm, Arm], ...] = (
    ("v2", "page_text"),
    ("plain_prompt", "page_text"),
    ("v2", "plain_prompt"),
)


# --- data ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmOutput:
    """One article with an output in an arm."""

    item_id: str
    n_chars: int  # page_text: the stripped output text; new arms: `n_output_chars`
    elements: tuple[ElementRecord, ...]


@dataclass(frozen=True)
class ArmData:
    arm: Arm
    n_items: int  # the comparison items
    outputs: tuple[ArmOutput, ...]  # the articles with an output, in sample order
    labels: Mapping[str, HumanLabel]  # element id -> the label that counts for this arm
    failed: int = 0  # v2 generations that failed validation
    open_errors: int = 0  # items whose latest run is a technical error
    unextracted: int = 0  # outputs without a successful extraction (new arms)
    models: tuple[str, ...] = ()
    prompt_versions: tuple[str, ...] = ()
    fingerprints: tuple[str, ...] = ()
    settings: tuple[str, ...] = ()
    extractors: tuple[str, ...] = ()

    @property
    def elements(self) -> list[ElementRecord]:
        return [e for o in self.outputs for e in o.elements]

    @property
    def labeled(self) -> int:
        return sum(e.element_id in self.labels for e in self.elements)

    @property
    def pending(self) -> int:
        return len(self.elements) - self.labeled

    @property
    def complete(self) -> bool:
        """Every element of its articles with an output has a label. An arm with no output yet,
        or with an output not extracted yet, is not complete either."""
        return bool(self.outputs) and self.unextracted == 0 and self.pending == 0


@dataclass(frozen=True)
class ValidatorCounters:
    """v2 generations without a technical error (ADR-0010, point 10): no label involved."""

    generations: int
    first_attempt_issues: Mapping[str, int]  # issue kind -> number of issues
    retried: int
    failed: int
    kept_quotes: int
    removed_quotes: int


@dataclass(frozen=True)
class ReportData:
    arms: tuple[ArmData, ...]  # in the order of `ARMS`
    validator: ValidatorCounters
    exports: tuple[ExportRecord, ...]
    current_guide_sha256: str | None  # None: the guide file is missing
    checks: tuple[CheckRecord, ...]
    n_label_records: int  # records in the comparison's labels file
    labels_changed: int

    def arm(self, name: Arm) -> ArmData:
        return next(a for a in self.arms if a.arm == name)


# --- statistics (pure) -----------------------------------------------------------------------


@dataclass(frozen=True)
class PairedDiff:
    estimate: float  # rate_a − rate_b, pooled over the paired articles; nan if an arm has none
    low: float
    high: float
    n_pairs: int  # paired articles
    skipped: int  # resamples where an arm had no element


def _pooled(clusters: Sequence[tuple[int, int]]) -> float:
    denominator = sum(d for _, d in clusters)
    return sum(n for n, _ in clusters) / denominator if denominator else math.nan


def paired_bootstrap_diff(
    a: Sequence[tuple[int, int]],
    b: Sequence[tuple[int, int]],
    *,
    n_boot: int = N_BOOT,
    alpha: float = ALPHA,
    seed: int = BOOT_SEED,
) -> PairedDiff:
    """Percentile cluster bootstrap of `rate_a − rate_b`, each rate a ratio of sums.

    `a[i]` and `b[i]` are the `(numerator, denominator)` of the same article in the two arms.
    Each resample draws `n` indices with `rng.randrange(n)`, as `cluster_bootstrap_ci` does, and
    applies them to both arms. A resample where either arm has no element is skipped and counted.
    """
    if len(a) != len(b):
        raise ValueError(f"Paired clusters differ in length: {len(a)} and {len(b)}.")
    n = len(a)
    estimate = _pooled(a) - _pooled(b)
    if n == 0:
        return PairedDiff(math.nan, math.nan, math.nan, 0, 0)
    rng = random.Random(seed)
    estimates: list[float] = []
    skipped = 0
    for _ in range(n_boot):
        indices = [rng.randrange(n) for _ in range(n)]
        den_a = sum(a[i][1] for i in indices)
        den_b = sum(b[i][1] for i in indices)
        if not den_a or not den_b:
            skipped += 1
            continue
        estimates.append(
            sum(a[i][0] for i in indices) / den_a - sum(b[i][0] for i in indices) / den_b
        )
    if not estimates:
        return PairedDiff(estimate, math.nan, math.nan, n, skipped)
    estimates.sort()
    low = estimates[int(alpha / 2 * (len(estimates) - 1))]
    high = estimates[int((1 - alpha / 2) * (len(estimates) - 1))]
    return PairedDiff(estimate, low, high, n, skipped)


def _is_unsupported(label: HumanLabel) -> bool:
    return label.label in _UNSUPPORTED


def _is_misattributed(element: ElementRecord, label: HumanLabel) -> bool:
    """A quote labeled CONTRADICTED whose note contains `locuteur` (ADR-0013, point 9)."""
    return (
        element.type == "quote"
        and label.label == "CONTRADICTED"
        and "locuteur" in label.note.casefold()
    )


def _clusters(
    arm: ArmData,
    counted: Callable[[ElementRecord, HumanLabel], bool],
    kept: Callable[[ElementRecord], bool] = lambda e: True,
    outputs: Iterable[ArmOutput] | None = None,
) -> list[tuple[int, int]]:
    """One `(counted, kept)` pair per article with an output; `(0, 0)` when it has none kept.
    Every kept element must have a label."""
    clusters: list[tuple[int, int]] = []
    for output in arm.outputs if outputs is None else outputs:
        elements = [e for e in output.elements if kept(e)]
        hits = sum(counted(e, arm.labels[e.element_id]) for e in elements)
        clusters.append((hits, len(elements)))
    return clusters


def unsupported_clusters(
    arm: ArmData, outputs: Iterable[ArmOutput] | None = None
) -> list[tuple[int, int]]:
    return _clusters(arm, lambda e, lab: _is_unsupported(lab), outputs=outputs)


@dataclass(frozen=True)
class ArmStats:
    unsupported: RateCI
    without_quotes: RateCI
    contradicted: RateCI
    articles_with_unsupported: RateCI
    quotes_stated: int
    quotes_unsupported: RateCI
    misattributed: int


def _ci(clusters: Sequence[tuple[int, int]]) -> RateCI:
    return cluster_bootstrap_ci(clusters, n_boot=N_BOOT, alpha=ALPHA, seed=BOOT_SEED)


def arm_stats(arm: ArmData) -> ArmStats:
    """The per-arm results of a complete arm (ADR-0010, point 10)."""
    if not arm.complete:
        raise ValueError(f"{arm.arm} is not complete: no statistic before every label is in.")
    per_article = unsupported_clusters(arm)
    elements = arm.elements
    quotes = [e for e in elements if e.type == "quote"]
    return ArmStats(
        unsupported=_ci(per_article),
        without_quotes=_ci(
            _clusters(arm, lambda e, lab: _is_unsupported(lab), lambda e: e.type != "quote")
        ),
        contradicted=_ci(_clusters(arm, lambda e, lab: lab.label == "CONTRADICTED")),
        articles_with_unsupported=_ci([(int(n > 0), 1) for n, _ in per_article]),
        quotes_stated=len(quotes),
        quotes_unsupported=_ci(
            _clusters(arm, lambda e, lab: _is_unsupported(lab), lambda e: e.type == "quote")
        ),
        misattributed=sum(_is_misattributed(e, arm.labels[e.element_id]) for e in quotes),
    )


def median_length(arm: ArmData) -> float | None:
    return statistics.median(o.n_chars for o in arm.outputs) if arm.outputs else None


@dataclass(frozen=True)
class ElementRatio:
    """Elements of an arm against those of `page_text`, on the articles both have an output."""

    n_arm: int
    n_page_text: int
    n_articles: int

    @property
    def below_two_thirds(self) -> bool:
        """The second guard of ADR-0010, point 12, in integers: n_arm / n_page_text < 2 / 3."""
        return self.n_page_text > 0 and 3 * self.n_arm < 2 * self.n_page_text


def element_ratio(arm: ArmData, page_text: ArmData) -> ElementRatio:
    shared = {o.item_id for o in arm.outputs} & {o.item_id for o in page_text.outputs}
    return ElementRatio(
        n_arm=sum(len(o.elements) for o in arm.outputs if o.item_id in shared),
        n_page_text=sum(len(o.elements) for o in page_text.outputs if o.item_id in shared),
        n_articles=len(shared),
    )


def difference(a: ArmData, b: ArmData) -> PairedDiff | None:
    """`a − b` of the unsupported rate over the articles with an output in both, in sample order
    (the order of `a.outputs`); None while either arm is incomplete."""
    if not (a.complete and b.complete):
        return None
    in_b = {o.item_id: o for o in b.outputs}
    paired = [o for o in a.outputs if o.item_id in in_b]
    return paired_bootstrap_diff(
        unsupported_clusters(a, paired),
        unsupported_clusters(b, [in_b[o.item_id] for o in paired]),
    )


def conclusion(diff: PairedDiff | None, failed_v2: int) -> str:
    """The sentence of ADR-0010, point 11, chosen from the interval of `v2 − page_text`
    (`None` while either arm is incomplete), after the first guard of point 12."""
    if diff is None:
        return "No conclusion is given until every label is in."
    if failed_v2 > MAX_FAILED_V2:
        return (
            f"{failed_v2} v2 generations failed validation (more than {MAX_FAILED_V2}): the "
            "guard of ADR-0010, point 12 applies, and no conclusion is given until the cause "
            "is understood."
        )
    if math.isnan(diff.estimate) or math.isnan(diff.low) or math.isnan(diff.high):
        return (
            "No conclusion: the interval of v2 − page_text cannot be computed "
            f"({diff.n_pairs} paired articles, {diff.skipped} resamples skipped)."
        )
    if diff.high < 0:
        reduction = (
            f"{_points(-diff.estimate)} points [{_points(-diff.high)}; {_points(-diff.low)}]"
        )
        return f"v2 lowers the unsupported rate by {reduction} on the article task."
    if diff.low > 0:
        return (
            f"v2 is worse on the article task: v2 − page_text = {fmt_diff(diff)}. The cause must "
            "be investigated before any real use."
        )
    return (
        f"No detectable difference on {diff.n_pairs} articles: v2 − page_text = {fmt_diff(diff)}."
    )


def labels_changed(records: Iterable[HumanLabel]) -> int:
    """Elements whose label value differs between two consecutive records of the append-only
    labels file, each counted once (a note change alone does not count)."""
    last: dict[str, str] = {}
    changed: set[str] = set()
    for record in records:
        previous = last.get(record.element_id)
        if previous is not None and previous != record.label:
            changed.add(record.element_id)
        last[record.element_id] = record.label
    return len(changed)


# --- formatting (pure) -----------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{100 * x:.1f}"


def fmt_rate(ci: RateCI) -> str:
    """`6.3 % [3.1–10.2 %] (12/190)`, or `n/a` when the denominator is 0."""
    if ci.denominator == 0:
        return "n/a"
    return f"{_pct(ci.rate)} % [{_pct(ci.low)}–{_pct(ci.high)} %] ({ci.numerator}/{ci.denominator})"


def _points(x: float, *, sign: bool = False) -> str:
    """A rate difference in points, one decimal, with a true minus sign; `+` only if `sign`."""
    value = round(100 * x, 1)
    if value == 0:
        return "0.0"
    text = f"{value:+.1f}" if sign else f"{value:.1f}"
    return text.replace("-", "−")


def fmt_diff(diff: PairedDiff) -> str:
    """`−2.4 points [−6.0; 1.1]`, or `n/a`."""
    if math.isnan(diff.estimate):
        return "n/a"
    interval = "[n/a]" if math.isnan(diff.low) else f"[{_points(diff.low)}; {_points(diff.high)}]"
    return f"{_points(diff.estimate, sign=True)} points {interval}"


def _chars(value: float | None) -> str:
    if value is None:
        return "n/a"
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def fmt_ratio(ratio: ElementRatio) -> str:
    if ratio.n_page_text == 0:
        return "n/a"
    text = (
        f"{_pct(ratio.n_arm / ratio.n_page_text)} % ({ratio.n_arm}/{ratio.n_page_text} on "
        f"{ratio.n_articles} articles)"
    )
    if ratio.below_two_thirds:
        text += (
            ": fewer than two thirds of the elements of page_text, a lower rate may come from "
            "stating less (ADR-0010, point 12)"
        )
    return text


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _joined(values: Sequence[str]) -> str:
    return "; ".join(values) if values else "n/a"


# --- rendering (pure) ------------------------------------------------------------------------


def _header(data: ReportData) -> list[str]:
    lines = [
        "## Settings",
        "",
        "| Arm | Model | Prompt version | Prompt fingerprint | Settings sent | Extractor |",
        "|---|---|---|---|---|---|",
    ]
    for a in data.arms:
        new_arm = a.arm != "page_text"
        lines.append(
            f"| {a.arm} | {_joined(a.models)} | {_joined(a.prompt_versions)} "
            f"| {_joined(a.fingerprints) if new_arm else NONE} "
            f"| {_joined(a.settings) if new_arm else 'model defaults'} | {_joined(a.extractors)} |"
        )
    lines += [
        "",
        f"Intervals: percentile cluster bootstrap over articles, {N_BOOT:,} resamples, seed "
        f"{BOOT_SEED}, alpha {ALPHA} (95 %), index convention of the v1 report.",
        "",
    ]
    if data.exports:
        lines.append("Label sheet exports:")
        lines += [
            f"- {_when(e.exported_at)}: seed {e.seed}, {e.n_rows} rows, guide SHA-256 "
            f"{e.guide_sha256}"
            for e in data.exports
        ]
    else:
        lines.append("No label sheet exported yet.")
    if data.current_guide_sha256 is None:
        lines.append("The labeling guide file is missing.")
    else:
        lines.append(f"Current labeling guide SHA-256: {data.current_guide_sha256}")
    hashes = {e.guide_sha256 for e in data.exports}
    current_differs = (
        bool(data.exports)
        and data.current_guide_sha256 is not None
        and data.current_guide_sha256 != data.exports[0].guide_sha256
    )
    if len(hashes) > 1 or current_differs:
        lines.append("**The labeling guide changed after the first export** (ADR-0010, point 8).")
    return [*lines, ""]


def _status(data: ReportData) -> list[str]:
    lines = [
        "## Status",
        "",
        "| Arm | Articles with an output | v2 generations failed validation "
        "| Technical errors open | Elements | Labeled | Pending |",
        "|---|---|---|---|---|---|---|",
    ]
    notes: list[str] = []
    for a in data.arms:
        failed = str(a.failed) if a.arm == "v2" else NONE
        lines.append(
            f"| {a.arm} | {len(a.outputs)} of {a.n_items} | {failed} | {a.open_errors} "
            f"| {len(a.elements)} | {a.labeled} | {a.pending} |"
        )
        if not a.outputs:
            notes.append(f"{a.arm}: no article with an output yet.")
        if a.unextracted:
            notes.append(
                f"{a.arm}: {a.unextracted} articles with an output have no successful "
                "extraction yet; the arm is not complete."
            )
    lines.append("")
    if notes:
        lines += [*notes, ""]
    return lines


def _results(data: ReportData, stats: Mapping[Arm, ArmStats]) -> list[str]:
    page_text = data.arm("page_text")
    lines = [
        "## Results per arm",
        "",
        "Shown for an arm once every element of its articles with an output has a label.",
        "",
        "| Arm | Unsupported | Unsupported without quotes | Contradicted "
        "| Articles with ≥1 unsupported | Elements | Median article length (chars) "
        "| Elements against page_text |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in data.arms:
        s = stats.get(a.arm)
        rates = (
            [PENDING] * 4
            if s is None
            else [
                fmt_rate(s.unsupported),
                fmt_rate(s.without_quotes),
                fmt_rate(s.contradicted),
                fmt_rate(s.articles_with_unsupported),
            ]
        )
        ratio = NONE if a.arm == "page_text" else fmt_ratio(element_ratio(a, page_text))
        lines.append(
            f"| {a.arm} | {' | '.join(rates)} | {len(a.elements)} "
            f"| {_chars(median_length(a))} | {ratio} |"
        )
    return [*lines, ""]


def _quotes(data: ReportData, stats: Mapping[Arm, ArmStats]) -> list[str]:
    lines = [
        "## Quotes",
        "",
        "Misattributed: a quote labeled CONTRADICTED whose note contains `locuteur`. Kept and "
        "removed: by the validator of v2 (ADR-0011), from the runs.",
        "",
        "| Arm | Stated | Unsupported | Misattributed | Kept | Removed |",
        "|---|---|---|---|---|---|",
    ]
    for a in data.arms:
        s = stats.get(a.arm)
        if s is None:
            cells = [PENDING] * 3 + [PENDING if a.arm == "v2" else NONE] * 2
        else:
            v2 = a.arm == "v2"
            counters = data.validator
            cells = [
                str(s.quotes_stated),
                fmt_rate(s.quotes_unsupported),
                str(s.misattributed),
                str(counters.kept_quotes) if v2 else NONE,
                str(counters.removed_quotes) if v2 else NONE,
            ]
        lines.append(f"| {a.arm} | {' | '.join(cells)} |")
    return [*lines, ""]


def _differences(data: ReportData, diffs: Mapping[tuple[Arm, Arm], PairedDiff | None]) -> list[str]:
    lines = [
        "## Differences of the unsupported rate",
        "",
        "Paired over the articles with an output in both arms; each resample takes the same "
        "articles in both.",
        "",
        "| Difference | Estimate [95 % interval] | Paired articles | Resamples skipped |",
        "|---|---|---|---|",
    ]
    for a, b in DIFFERENCES:
        name = f"{a} − {b}" + (" (primary)" if (a, b) == DIFFERENCES[0] else "")
        diff = diffs[(a, b)]
        if diff is None:
            lines.append(f"| {name} | {PENDING} | {PENDING} | {PENDING} |")
        else:
            lines.append(f"| {name} | {fmt_diff(diff)} | {diff.n_pairs} | {diff.skipped} |")
    lines += [
        "",
        "A gain is attributed to the architecture only for the part v2 − plain_prompt; the part "
        "plain_prompt − page_text comes from the prompt and the generation settings (ADR-0010, "
        "point 11).",
        "",
    ]
    return lines


def _validator(counters: ValidatorCounters) -> list[str]:
    kinds = [k.value for k in IssueKind]
    kinds += sorted(set(counters.first_attempt_issues) - set(kinds))
    lines = [
        "## Validator counters of v2",
        "",
        f"No label involved. Over the {counters.generations} v2 generations without a "
        "technical error.",
        "",
        "| Counter | Count |",
        "|---|---|",
    ]
    lines += [
        f"| First-attempt issues: {kind} | {counters.first_attempt_issues.get(kind, 0)} |"
        for kind in kinds
    ]
    lines += [
        f"| Runs that used the retry | {counters.retried} |",
        f"| Generations that failed validation | {counters.failed} |",
        f"| Quotes kept | {counters.kept_quotes} |",
        f"| Quotes removed | {counters.removed_quotes} |",
        "",
    ]
    return lines


def _checks(data: ReportData) -> list[str]:
    lines = ["## Label checks", ""]
    if data.checks:
        lines += [
            "| Date | Labels checked | Supported quotes not found | Same value, different labels "
            "| `locuteur` outside a contradicted quote |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {_when(c.checked_at)} | {c.n_labels} | {c.quote_supported_not_found} "
            f"| {c.inconsistent_values} | {c.locuteur_notes} |"
            for c in data.checks
        ]
    else:
        lines.append("No label check run yet.")
    lines.append("")
    if data.n_label_records:
        lines.append(
            f"Elements whose label value changed in the comparison's labels file: "
            f"{data.labels_changed}."
        )
    else:
        lines.append("No comparison label imported yet.")
    return [*lines, ""]


LIMITS = (
    "With 16 articles, a percentile bootstrap interval tends to be somewhat too narrow.",
    "One labeler, the project's author.",
    "The 74 earlier page_text labels were given unblinded, under the v1 labeling guide.",
    "The new arms were extracted later than page_text, by a preview model that may have changed.",
    "The metric sees entities and quotes only: an unsupported claim with no name, number, date or "
    "quote is invisible in every arm.",
)


def render_report(data: ReportData) -> str:
    stats = {a.arm: arm_stats(a) for a in data.arms if a.complete}
    arms = {a.arm: a for a in data.arms}
    diffs = {(a, b): difference(arms[a], arms[b]) for a, b in DIFFERENCES}
    lines = [
        "# v2 comparison — unsupported elements on the article task",
        "",
        "Protocol: ADR-0010; harness: ADR-0013. Unsupported = CONTRADICTED + NOT_IN_SOURCE. A "
        "verdict is a human label only: an element without a label is pending. An arm with a "
        "pending label gets no rate, no per-type count, no difference and no conclusion.",
        "",
        *_header(data),
        *_status(data),
        *_results(data, stats),
        *_quotes(data, stats),
        *_differences(data, diffs),
        "## Conclusion",
        "",
        conclusion(diffs[DIFFERENCES[0]], data.arm("v2").failed),
        "",
        *_validator(data.validator),
        *_checks(data),
        "## Limits",
        "",
        *(f"- {limit}" for limit in LIMITS),
        "",
    ]
    return "\n".join(lines)


# --- loading ---------------------------------------------------------------------------------


def _extractor(record: ExtractionRecord) -> str:
    return f"{record.model} ({record.thinking_level or 'default thinking'})"


def _settings(settings: Mapping[str, float | str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(settings.items())) or "none"


def _by_run(elements: Iterable[ElementRecord]) -> dict[str, list[ElementRecord]]:
    by_run: dict[str, list[ElementRecord]] = {}
    for e in elements:
        by_run.setdefault(e.run_id, []).append(e)
    return by_run


def _load_page_text(
    paths: ComparePaths, item_ids: Sequence[str], labels: Mapping[str, HumanLabel]
) -> ArmData:
    """The baseline's latest successful `page_text / article` runs of the comparison items that
    have a successful extraction."""
    baseline = paths.baseline
    runs = latest_by(read_jsonl(baseline.runs, RunRecord), lambda r: r.run_id)
    extractions = latest_by(read_jsonl(baseline.extractions, ExtractionRecord), lambda x: x.run_id)
    elements = _by_run(read_jsonl(baseline.elements, ElementRecord))
    outputs: list[ArmOutput] = []
    errors = 0
    models: set[str] = set()
    versions: set[str] = set()
    extractors: set[str] = set()
    for item_id in item_ids:
        run = runs.get(f"{item_id}:page_text:article:0")
        if run is None:
            continue
        if run.error is not None:
            errors += 1
            continue
        extraction = extractions.get(run.run_id)
        if extraction is None or extraction.error is not None:
            continue
        outputs.append(
            ArmOutput(
                item_id, len((run.output_text or "").strip()), tuple(elements.get(run.run_id, []))
            )
        )
        models.add(run.model)
        versions.add(run.prompt_version)
        extractors.add(_extractor(extraction))
    return ArmData(
        arm="page_text",
        n_items=len(item_ids),
        outputs=tuple(outputs),
        labels=labels,
        open_errors=errors,
        models=tuple(sorted(models)),
        prompt_versions=tuple(sorted(versions)),
        extractors=tuple(sorted(extractors)),
    )


def _load_new_arm(
    condition: CompareCondition,
    item_ids: Sequence[str],
    runs: Mapping[str, CompareRun],
    extractions: Mapping[str, ExtractionRecord],
    elements: Mapping[str, list[ElementRecord]],
    labels: Mapping[str, HumanLabel],
) -> ArmData:
    """An article has an output when its latest run has no technical error and, for v2, the
    generation passed validation."""
    outputs: list[ArmOutput] = []
    errors = failed = unextracted = 0
    models: set[str] = set()
    versions: set[str] = set()
    fingerprints: set[str] = set()
    settings: set[str] = set()
    extractors: set[str] = set()
    for item_id in item_ids:
        run = runs.get(compare_run_id(item_id, condition))
        if run is None:
            continue
        if run.error is not None:
            errors += 1
            continue
        models.add(run.model)
        versions.add(run.prompt_version)
        fingerprints.add(run.prompt_fingerprint)
        settings.add(_settings(run.settings))
        if condition == "v2" and run.outcome != "ok":
            failed += run.outcome == "failed"
            continue
        extraction = extractions.get(run.run_id)
        if extraction is None or extraction.error is not None:
            unextracted += 1
        else:
            extractors.add(_extractor(extraction))
        outputs.append(
            ArmOutput(item_id, run.n_output_chars or 0, tuple(elements.get(run.run_id, [])))
        )
    return ArmData(
        arm=condition,
        n_items=len(item_ids),
        outputs=tuple(outputs),
        labels=labels,
        failed=failed,
        open_errors=errors,
        unextracted=unextracted,
        models=tuple(sorted(models)),
        prompt_versions=tuple(sorted(versions)),
        fingerprints=tuple(sorted(fingerprints)),
        settings=tuple(sorted(settings)),
        extractors=tuple(sorted(extractors)),
    )


def _validator_counters(runs: Iterable[CompareRun]) -> ValidatorCounters:
    good = [r for r in runs if r.error is None]
    issues: Counter[str] = Counter()
    for r in good:
        if r.attempts:
            issues.update(r.attempts[0].issues)
    return ValidatorCounters(
        generations=len(good),
        first_attempt_issues=dict(issues),
        retried=sum(bool(r.retried) for r in good),
        failed=sum(r.outcome == "failed" for r in good),
        kept_quotes=sum(r.kept_quotes or 0 for r in good),
        removed_quotes=sum(r.removed_quotes or 0 for r in good),
    )


def load_report_data(paths: ComparePaths) -> ReportData:
    """Reads every file the report needs; a file not written yet reads as empty."""
    item_ids = [i.item_id for i in comparison_items(paths)]
    compare_labels = latest_labels(paths)
    baseline_labels = latest_by(
        read_jsonl(paths.baseline.labels, HumanLabel), lambda lab: lab.element_id
    )
    # page_text: the baseline's labels plus the comparison's, the comparison's winning.
    page_text = _load_page_text(paths, item_ids, {**baseline_labels, **compare_labels})

    runs = latest_by(read_jsonl(paths.runs, CompareRun), lambda r: r.run_id)
    extractions = latest_by(read_jsonl(paths.extractions, ExtractionRecord), lambda x: x.run_id)
    elements = _by_run(read_jsonl(paths.elements, ElementRecord))
    new_arms = {
        condition: _load_new_arm(condition, item_ids, runs, extractions, elements, compare_labels)
        for condition in ("plain_prompt", "v2")
    }
    v2_runs = [r for i in item_ids if (r := runs.get(compare_run_id(i, "v2"))) is not None]

    try:
        current_guide: str | None = guide_sha256(paths.labeling_guide)
    except CompareError:
        current_guide = None
    label_records = read_jsonl(paths.labels, HumanLabel)
    return ReportData(
        arms=(page_text, new_arms["plain_prompt"], new_arms["v2"]),
        validator=_validator_counters(v2_runs),
        exports=tuple(read_jsonl(paths.export_log, ExportRecord)),
        current_guide_sha256=current_guide,
        checks=tuple(read_jsonl(paths.check_log, CheckRecord)),
        n_label_records=len(label_records),
        labels_changed=labels_changed(label_records),
    )


def build_report(paths: ComparePaths) -> str:
    return render_report(load_report_data(paths))
