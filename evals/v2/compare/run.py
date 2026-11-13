"""The `run` step of the comparison (ADR-0013, point 2): the `v2` and `plain_prompt` arms on
the article task, once per non-opaque sample item, on `cap_text` of the frozen snapshot text.

- Before the first call, it refuses to start when a prompt fingerprint differs from its pin, when
  a snapshot is missing or does not match its recorded hash, or when the model differs from the
  one of the baseline's `page_text / article` runs (ADR-0010, point 2).
- A run whose latest record has no error is never run again (no `--force`). A technical error is
  recorded and retried by running the step again; `runs.jsonl` is append-only, so the error
  record stays. A v2 generation that fails validation is a result, not an error.
- It prints counters only, never model text: the labeler must not see the outputs.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from dzdia_newsroom.llm import StructuredGenerator, TextGenerator
from dzdia_newsroom.tools.article import PROMPT_VERSION as V2_PROMPT_VERSION
from dzdia_newsroom.tools.evidence import cap_text
from evals.baseline_v1.jsonl import append_jsonl, latest_by, read_jsonl
from evals.baseline_v1.schemas import RunRecord, SampleItem, SnapshotMeta
from evals.baseline_v1.snapshot import load_snapshot_text, sha256_text, text_path
from evals.v2.arms import (
    PLAIN_PROMPT_FINGERPRINT,
    PLAIN_PROMPT_VERSION,
    PLAIN_SETTINGS,
    V2_PROMPT_FINGERPRINT,
    V2_SETTINGS,
    frozen_prompt_mismatches,
    run_plain,
    run_v2,
)
from evals.v2.compare import CompareError
from evals.v2.compare.paths import ComparePaths
from evals.v2.records import AttemptRecord, OutputFields, issue_kinds, plain_fields, v2_fields

CompareCondition = Literal["plain_prompt", "v2"]
# Call order for each article: v2 first, then plain_prompt (as in the tryout).
COMPARE_CONDITIONS: tuple[CompareCondition, ...] = ("v2", "plain_prompt")
TASK: Literal["article"] = "article"
REPEAT = 0
# ADR-0010, point 12: with more than this many failed v2 generations, no headline comparison.
MAX_FAILED_V2 = 2


class CompareRun(BaseModel):
    run_id: str  # f"{item_id}:{condition}:article:0"
    item_id: str
    condition: CompareCondition
    task: Literal["article"] = TASK
    repeat: int = REPEAT
    model: str
    prompt_version: str
    prompt_fingerprint: str
    settings: dict[str, float | str]  # what was actually sent (ADR-0010, point 13)
    created_at: datetime
    text_sha256: str  # of the full snapshot text, before `cap_text`
    truncated: bool  # the snapshot text was cut by `cap_text`
    n_source_chars: int  # length of the capped text the arm received
    error: str | None = None  # technical error (API, timeout): re-running the step retries it
    # Both arms, when there is an output. v2: summed over the attempts.
    n_output_chars: int | None = None  # length of the stripped output (v2: rendered) text
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: int | None = None
    # v2 only
    outcome: Literal["ok", "failed"] | None = None
    n_evidence: int | None = None
    attempts: list[AttemptRecord] = []
    retried: bool | None = None
    removed_quotes: int | None = None
    kept_quotes: int | None = None
    n_sentences: int | None = None
    rendered_text: str | None = None
    draft: dict[str, Any] | None = None
    # plain_prompt only
    output_text: str | None = None


def compare_run_id(item_id: str, condition: CompareCondition) -> str:
    return f"{item_id}:{condition}:{TASK}:{REPEAT}"


class CompareLLM(TextGenerator, StructuredGenerator, Protocol):
    """Both arms run on the same client."""


# --- guards ----------------------------------------------------------------------------------


def check_fingerprints() -> None:
    """Refuses unless both prompts are the frozen ones (ADR-0010, point 4)."""
    mismatches = frozen_prompt_mismatches()
    if mismatches:
        raise CompareError("\n".join(mismatches))


def comparison_items(paths: ComparePaths, item_ids: Sequence[str] = ()) -> list[SampleItem]:
    """The non-opaque sample items in sample order, restricted to `item_ids` when given."""
    items = [i for i in read_jsonl(paths.baseline.sample, SampleItem) if not i.is_opaque]
    if not items:
        raise CompareError(f"No non-opaque item in {paths.baseline.sample}.")
    if item_ids:
        unknown = sorted(set(item_ids) - {i.item_id for i in items})
        if unknown:
            raise CompareError(f"Unknown or opaque item ids: {unknown}")
        items = [i for i in items if i.item_id in item_ids]
    return items


@dataclass(frozen=True)
class ItemInput:
    item: SampleItem
    text: str  # capped: what both arms receive
    truncated: bool
    text_sha256: str  # of the full snapshot text


def load_inputs(paths: ComparePaths, items: Iterable[SampleItem]) -> list[ItemInput]:
    """Each item's snapshot text, read like the baseline reads it and checked against
    `text_sha256` of the latest meta of its snapshot in the baseline's `snapshots.jsonl`."""
    baseline = paths.baseline
    metas = latest_by(read_jsonl(baseline.snapshots_meta, SnapshotMeta), lambda m: m.snapshot_id)
    inputs: list[ItemInput] = []
    for item in items:
        meta = metas.get(item.snapshot_id)
        if meta is None:
            raise CompareError(
                f"Missing snapshot: no meta for {item.snapshot_id} in {baseline.snapshots_meta}."
            )
        path = text_path(baseline.snapshot_dir, item.snapshot_id)
        if not path.exists():
            raise CompareError(f"Missing snapshot text {path}.")
        text = load_snapshot_text(baseline.snapshot_dir, item.snapshot_id)
        if sha256_text(text) != meta.text_sha256:
            raise CompareError(
                f"{path} does not match text_sha256 of {item.snapshot_id} in "
                f"{baseline.snapshots_meta}: the frozen text changed."
            )
        capped, truncated = cap_text(text)
        inputs.append(ItemInput(item, capped, truncated, meta.text_sha256))
    return inputs


def check_model(paths: ComparePaths, model: str) -> None:
    """Refuses unless every successful `page_text / article` run of the baseline used `model`
    (ADR-0010, point 2: all arms use the same model)."""
    runs = latest_by(read_jsonl(paths.baseline.runs, RunRecord), lambda r: r.run_id).values()
    models = {
        r.model
        for r in runs
        if r.condition == "page_text" and r.task == "article" and r.error is None
    }
    if not models:
        raise CompareError(f"No successful page_text / article run in {paths.baseline.runs}.")
    if models != {model}:
        raise CompareError(
            f"Model {model} differs from the baseline's page_text / article runs "
            f"({', '.join(sorted(models))})."
        )


# --- run -------------------------------------------------------------------------------------


async def _execute(llm: CompareLLM, given: ItemInput, condition: CompareCondition) -> CompareRun:
    v2 = condition == "v2"
    record = CompareRun(
        run_id=compare_run_id(given.item.item_id, condition),
        item_id=given.item.item_id,
        condition=condition,
        model=llm.model,
        prompt_version=V2_PROMPT_VERSION if v2 else PLAIN_PROMPT_VERSION,
        prompt_fingerprint=V2_PROMPT_FINGERPRINT if v2 else PLAIN_PROMPT_FINGERPRINT,
        settings=V2_SETTINGS if v2 else PLAIN_SETTINGS,
        created_at=datetime.now(UTC),
        text_sha256=given.text_sha256,
        truncated=given.truncated,
        n_source_chars=len(given.text),
    )
    try:
        fields: OutputFields = (
            v2_fields(await run_v2(llm, given.text))
            if v2
            else plain_fields(await run_plain(llm, given.text))
        )
    except Exception as exc:  # a technical error must not abort the batch
        return record.model_copy(update={"error": repr(exc)})
    return record.model_copy(update=fields)


def ordered(conditions: Iterable[CompareCondition]) -> list[CompareCondition]:
    """The given conditions in call order, without duplicates."""
    given = set(conditions)
    return [c for c in COMPARE_CONDITIONS if c in given]


async def run_compare(
    paths: ComparePaths,
    llm: CompareLLM,
    *,
    conditions: Sequence[CompareCondition] = COMPARE_CONDITIONS,
    item_ids: Sequence[str] = (),
    on_record: Callable[[CompareRun], None] = lambda record: None,
) -> list[CompareRun]:
    """The `run` step: every guard before the first call, then sequential calls, each record
    appended as soon as its call ends. Returns the records written by this call."""
    check_fingerprints()
    inputs = load_inputs(paths, comparison_items(paths, item_ids))
    check_model(paths, llm.model)
    previous = latest_by(read_jsonl(paths.runs, CompareRun), lambda r: r.run_id)
    done = {run_id for run_id, r in previous.items() if r.error is None}

    records: list[CompareRun] = []
    for given in inputs:
        for condition in ordered(conditions):
            if compare_run_id(given.item.item_id, condition) in done:
                continue
            record = await _execute(llm, given, condition)
            append_jsonl(paths.runs, [record])
            on_record(record)
            records.append(record)
    return records


# --- output: counters only -------------------------------------------------------------------


def run_line(record: CompareRun) -> str:
    """One line of counters, no model text."""
    head = f"{record.item_id} {record.condition:<12}"
    if record.error:
        return f"{head} ERROR {record.error[:200]}"
    latency = f"{(record.latency_ms or 0) / 1000:.1f} s"
    if record.condition == "plain_prompt":
        return f"{head} {record.n_output_chars} chars, {latency}"
    issues = " | ".join(issue_kinds(a.issues) for a in record.attempts)
    return (
        f"{head} {record.outcome}, retried {'yes' if record.retried else 'no'}, issues {issues}, "
        f"kept {record.kept_quotes}, removed {record.removed_quotes}, "
        f"{record.n_output_chars} chars, {latency}"
    )


@dataclass(frozen=True)
class ConditionCounts:
    condition: CompareCondition
    ok: int  # no error; for v2, outcome "ok"
    failed: int  # v2 generations that failed validation
    errors: int  # latest record is a technical error
    missing: int  # no record
    retried: int  # v2 runs that used the retry
    kept_quotes: int
    removed_quotes: int


@dataclass(frozen=True)
class Summary:
    counts: tuple[ConditionCounts, ...]
    error_records: int  # every record with an error in the runs file, retried ones included

    @property
    def failed_v2(self) -> int:
        return sum(c.failed for c in self.counts if c.condition == "v2")


def summarize(
    paths: ComparePaths,
    conditions: Sequence[CompareCondition] = COMPARE_CONDITIONS,
    item_ids: Sequence[str] = (),
) -> Summary:
    """Counters over the latest record of every planned run, done now or earlier."""
    all_records = read_jsonl(paths.runs, CompareRun)
    latest = latest_by(all_records, lambda r: r.run_id)
    items = comparison_items(paths, item_ids)
    counts: list[ConditionCounts] = []
    for condition in ordered(conditions):
        records = [latest.get(compare_run_id(i.item_id, condition)) for i in items]
        present = [r for r in records if r is not None]
        good = [r for r in present if r.error is None]
        counts.append(
            ConditionCounts(
                condition=condition,
                ok=sum(r.outcome != "failed" for r in good),
                failed=sum(r.outcome == "failed" for r in good),
                errors=len(present) - len(good),
                missing=len(records) - len(present),
                retried=sum(bool(r.retried) for r in good),
                kept_quotes=sum(r.kept_quotes or 0 for r in good),
                removed_quotes=sum(r.removed_quotes or 0 for r in good),
            )
        )
    return Summary(
        counts=tuple(counts), error_records=sum(r.error is not None for r in all_records)
    )


def summary_lines(summary: Summary) -> list[str]:
    lines: list[str] = []
    for c in summary.counts:
        line = f"{c.condition:<12} ok {c.ok}"
        if c.condition == "v2":
            line += f", failed {c.failed}"
        line += f", technical errors {c.errors}, missing {c.missing}"
        if c.condition == "v2":
            line += (
                f"; retried {c.retried}, quotes kept {c.kept_quotes}, removed {c.removed_quotes}"
            )
        lines.append(line)
    lines.append(f"Error records in the runs file: {summary.error_records}")
    if summary.failed_v2 > MAX_FAILED_V2:
        lines.append(
            f"{summary.failed_v2} v2 generations failed (more than {MAX_FAILED_V2}): the guard of "
            "ADR-0010, point 12 applies, no headline comparison until the cause is understood."
        )
    return lines
