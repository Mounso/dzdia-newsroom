"""The `extract` and `match` steps of the comparison (ADR-0013, point 3; ADR-0010, point 5).

- `extract` feeds the baseline's `extract_run` unchanged with the plain output, or the rendered
  text of a v2 generation that passed validation. A failed v2 generation and a run with a
  technical error are never extracted. Extraction records are appended; the elements go to the
  comparison's `elements.jsonl` only. Before the first call, it refuses an extractor whose model
  or thinking level differs from the baseline's `page_text / article` extractions.
- `match` runs the baseline's matcher, with its default threshold, against the snapshot texts.
- Both print counters only: the labeler must not see the outputs or the matcher's verdicts.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from dzdia_newsroom.llm import StructuredGenerator
from evals.baseline_v1.extract import extract_run
from evals.baseline_v1.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from evals.baseline_v1.matcher import DEFAULT_NAME_THRESHOLD, SourceIndex, match_element
from evals.baseline_v1.schemas import ElementRecord, ExtractionRecord, RunRecord, SampleItem
from evals.baseline_v1.snapshot import load_snapshot_text
from evals.v2.compare import CompareError
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.run import CompareRun

# --- extract ---------------------------------------------------------------------------------


def extractable_text(run: CompareRun) -> str | None:
    """The text given to the extractor, or None when the run is not extracted: a technical
    error, a v2 generation that failed validation, or no output text."""
    if run.error is not None:
        return None
    if run.condition == "plain_prompt":
        return run.output_text
    if run.outcome != "ok":
        return None
    return run.rendered_text


def as_run_record(run: CompareRun, text: str) -> RunRecord:
    """The baseline record `extract_run` reads: same ids, so element ids start with the run id."""
    return RunRecord(
        run_id=run.run_id,
        item_id=run.item_id,
        condition=run.condition,
        task=run.task,
        repeat=run.repeat,
        model=run.model,
        prompt_version=run.prompt_version,
        created_at=run.created_at,
        output_text=text,
    )


def latest_runs(paths: ComparePaths) -> dict[str, CompareRun]:
    return latest_by(read_jsonl(paths.runs, CompareRun), lambda r: r.run_id)


def extractable_runs(runs: Iterable[CompareRun]) -> list[RunRecord]:
    """The extractable runs among `runs`, as baseline records."""
    records: list[RunRecord] = []
    for run in runs:
        text = extractable_text(run)
        if text is not None:
            records.append(as_run_record(run, text))
    return records


def check_extractor(paths: ComparePaths, model: str, thinking_level: str | None) -> None:
    """Refuses unless the latest successful extraction of every `page_text / article` run of
    the baseline used this model and thinking level (ADR-0013, point 3)."""
    baseline = paths.baseline
    runs = latest_by(read_jsonl(baseline.runs, RunRecord), lambda r: r.run_id)
    extractions = latest_by(read_jsonl(baseline.extractions, ExtractionRecord), lambda x: x.run_id)
    settings = {
        (x.model, x.thinking_level)
        for run_id, x in extractions.items()
        if x.error is None
        and run_id in runs
        and runs[run_id].condition == "page_text"
        and runs[run_id].task == "article"
    }
    if not settings:
        raise CompareError(
            f"No successful extraction of a page_text / article run in {baseline.extractions}."
        )
    if settings != {(model, thinking_level)}:
        found = ", ".join(sorted(f"{m} ({t})" for m, t in settings))
        raise CompareError(
            f"Extractor {model} ({thinking_level}) differs from the baseline's page_text / "
            f"article extractions ({found})."
        )


@dataclass(frozen=True)
class ExtractSummary:
    extracted: int  # runs extracted by this call, errors included
    errors: int
    n_elements: int  # elements in the comparison's elements.jsonl afterwards
    input_tokens: int
    output_tokens: int
    thinking_tokens: int


def pending_extractions(paths: ComparePaths) -> list[RunRecord]:
    """Extractable runs without a successful latest extraction (a failed one is retried)."""
    previous = latest_by(read_jsonl(paths.extractions, ExtractionRecord), lambda x: x.run_id)
    return [
        r
        for r in extractable_runs(latest_runs(paths).values())
        if r.run_id not in previous or previous[r.run_id].error is not None
    ]


async def extract_compare(paths: ComparePaths, generator: StructuredGenerator) -> ExtractSummary:
    """The `extract` step: the guard, then one extraction per pending run, each record appended
    and the elements file rewritten as soon as its call ends (as the baseline's `extract`)."""
    check_extractor(paths, generator.model, generator.thinking_level)
    todo = pending_extractions(paths)
    elements = {e.element_id: e for e in read_jsonl(paths.elements, ElementRecord)}
    errors = input_tokens = output_tokens = thinking_tokens = 0
    for next_done in asyncio.as_completed([extract_run(generator, r) for r in todo]):
        record, new_elements = await next_done
        append_jsonl(paths.extractions, [record])
        errors += record.error is not None
        input_tokens += record.input_tokens or 0
        output_tokens += record.output_tokens or 0
        thinking_tokens += record.thinking_tokens or 0
        elements = {k: v for k, v in elements.items() if v.run_id != record.run_id}
        elements.update({e.element_id: e for e in new_elements})
        write_jsonl(paths.elements, elements.values())
    return ExtractSummary(
        extracted=len(todo),
        errors=errors,
        n_elements=len(elements),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
    )


def extract_lines(summary: ExtractSummary) -> list[str]:
    return [
        f"Extracted {summary.extracted} runs, {summary.errors} extraction errors "
        "(re-run the step to retry them).",
        f"{summary.n_elements} elements in total.",
        # No price table in code: it would go stale silently. Thinking tokens are billed as output.
        f"Tokens this pass: input {summary.input_tokens:,}, output {summary.output_tokens:,}, "
        f"thinking {summary.thinking_tokens:,}",
    ]


# --- match -----------------------------------------------------------------------------------


def match_compare(paths: ComparePaths) -> int:
    """The `match` step: rewrites the comparison's elements with `match_status` and
    `match_score`, against the snapshot text of each element's sample item. Returns the number
    of elements matched."""
    items = {i.item_id: i for i in read_jsonl(paths.baseline.sample, SampleItem)}
    elements = read_jsonl(paths.elements, ElementRecord)
    indexes: dict[str, SourceIndex] = {}
    matched: list[ElementRecord] = []
    for e in elements:
        if e.item_id not in items:
            raise CompareError(f"Item {e.item_id} is not in {paths.baseline.sample}.")
        snapshot_id = items[e.item_id].snapshot_id
        if snapshot_id not in indexes:
            text = load_snapshot_text(paths.baseline.snapshot_dir, snapshot_id)
            indexes[snapshot_id] = SourceIndex(text)
        result = match_element(e.type, e.value, indexes[snapshot_id], DEFAULT_NAME_THRESHOLD)
        matched.append(
            e.model_copy(
                update={
                    "match_status": "found" if result.found else "not_found",
                    "match_score": result.score,
                }
            )
        )
    write_jsonl(paths.elements, matched)
    return len(matched)
