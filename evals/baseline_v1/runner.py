"""Run the frozen v1 prompts under each condition. Idempotent: successful run_ids are skipped."""

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from evals.baseline_v1.llm import TextGenerator
from evals.baseline_v1.schemas import Condition, RunRecord, SampleItem, Task
from evals.baseline_v1.v1_prompts import PROMPT_VERSION, build_prompt

REPEATS: Mapping[Task, int] = {"summary": 3, "article": 1}


@dataclass(frozen=True)
class RunJob:
    run_id: str
    item_id: str
    condition: Condition
    task: Task
    repeat: int
    prompt: str


def plan_jobs(
    items: Iterable[SampleItem],
    texts_by_snapshot: Mapping[str, str],
    conditions: Iterable[Condition],
    repeats: Mapping[Task, int] = REPEATS,
) -> list[RunJob]:
    conditions = list(conditions)
    jobs: list[RunJob] = []
    for item in items:
        for condition in conditions:
            if condition == "page_text" and item.is_opaque:
                continue  # same text as the original item: nothing new to measure
            # v1 forwarded the raw user message (the URL) as content to both prompts.
            content = item.url if condition == "url_only" else texts_by_snapshot[item.snapshot_id]
            for task, n in repeats.items():
                for repeat in range(n):
                    jobs.append(
                        RunJob(
                            run_id=f"{item.item_id}:{condition}:{task}:{repeat}",
                            item_id=item.item_id,
                            condition=condition,
                            task=task,
                            repeat=repeat,
                            prompt=build_prompt(task, content),
                        )
                    )
    return jobs


async def _execute(job: RunJob, generator: TextGenerator) -> RunRecord:
    record = RunRecord(
        run_id=job.run_id,
        item_id=job.item_id,
        condition=job.condition,
        task=job.task,
        repeat=job.repeat,
        model=generator.model,
        prompt_version=PROMPT_VERSION,
        created_at=datetime.now(UTC),
    )
    try:
        generation = await generator.generate(job.prompt)
    except Exception as exc:  # one failing call must not abort the whole batch
        return record.model_copy(update={"error": repr(exc)})
    return record.model_copy(
        update={
            "output_text": generation.text,
            "latency_ms": generation.latency_ms,
            "input_tokens": generation.input_tokens,
            "output_tokens": generation.output_tokens,
        }
    )


async def execute_jobs(
    jobs: Iterable[RunJob],
    generator: TextGenerator,
    done_run_ids: set[str],
    on_record: Callable[[RunRecord], None],
) -> list[RunRecord]:
    """Execute pending jobs concurrently (the generator bounds concurrency) and persist each result
    as soon as it completes, so an interrupted batch can be resumed."""
    pending = [job for job in jobs if job.run_id not in done_run_ids]
    records: list[RunRecord] = []
    for next_done in asyncio.as_completed([_execute(job, generator) for job in pending]):
        record = await next_done
        on_record(record)
        records.append(record)
    return records
