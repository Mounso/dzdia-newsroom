"""The temperature check (ADR-0013, point 10; ADR-0010, point 13), run as
`python -m evals.v2.tryout determinism`.

On the first tryout page of each source (in `pages.jsonl` order), with the frozen prompts (the
step refuses a prompt whose fingerprint differs from its pin):
the plain arm 3 times at temperature 0 (`run_plain`) and 3 times at 1.0, and the v2 arm 3 times
(temperature 0, set inside the pipeline). Each call records the hash and the length of its
output, never the text, in `evals/.data/v2_tryout/determinism.jsonl` (append-only). The step is
idempotent: a run_id recorded without error is skipped, a technical error is retried.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from dzdia_newsroom.tools.article import PROMPT_VERSION as V2_PROMPT_VERSION
from dzdia_newsroom.tools.evidence import cap_text
from evals.baseline_v1.jsonl import append_jsonl, latest_by, read_jsonl
from evals.baseline_v1.snapshot import sha256_text
from evals.v2.arms import (
    PLAIN_PROMPT_VERSION,
    PLAIN_TEMPERATURE,
    V2_SETTINGS,
    build_plain_prompt,
    frozen_prompt_mismatches,
    run_plain,
    run_v2,
)
from evals.v2.tryout import (
    Arm,
    TryoutError,
    TryoutLLM,
    TryoutPage,
    TryoutPaths,
    fingerprint_of,
    load_page_text,
)

REPEATS = 3
HOT_TEMPERATURE = 1.0
V2_TEMPERATURE = 0.0  # what `V2_SETTINGS` records: set by `GeminiClient.generate_structured`


@dataclass(frozen=True)
class Group:
    """One arm at one temperature: its repeats on one page should give one output."""

    arm: Arm
    temperature: float

    @property
    def label(self) -> str:
        return f"{self.arm:<5} t={self.temperature:g}"

    @property
    def settings(self) -> dict[str, float | str]:
        return V2_SETTINGS if self.arm == "v2" else {"temperature": self.temperature}


# In the order of the calls on each page.
GROUPS: tuple[Group, ...] = (
    Group("plain", PLAIN_TEMPERATURE),
    Group("plain", HOT_TEMPERATURE),
    Group("v2", V2_TEMPERATURE),
)


class DeterminismRun(BaseModel):
    run_id: str
    page_id: str
    arm: Arm
    temperature: float
    repeat: int
    model: str
    prompt_version: str
    prompt_fingerprint: str
    settings: dict[str, float | str]  # what was actually sent
    created_at: datetime
    error: str | None = None  # technical error (API, timeout): re-running the step retries it
    # Of the output text as returned (v2: the rendered text); None when v2 failed or on error.
    output_sha256: str | None = None
    n_output_chars: int | None = None
    # v2 only
    outcome: Literal["ok", "failed"] | None = None
    retried: bool | None = None


def determinism_run_id(
    page_id: str, group: Group, repeat: int, fingerprint: str, model: str
) -> str:
    return f"{page_id}:{group.arm}:t{group.temperature:g}:{repeat}:{fingerprint[:12]}:{model}"


def first_page_per_source(pages: Sequence[TryoutPage]) -> list[TryoutPage]:
    """The first page of each source, in the order of `pages`."""
    chosen: dict[str, TryoutPage] = {}
    for page in pages:
        chosen.setdefault(page.source, page)
    return list(chosen.values())


@dataclass(frozen=True)
class PlannedCall:
    page_id: str
    group: Group
    repeat: int
    fingerprint: str
    run_id: str


def plan_calls(page_ids: Sequence[str], model: str) -> list[PlannedCall]:
    """Every call of the step, in call order: page, group, repeat."""
    fingerprints = {arm: fingerprint_of(arm) for arm in {g.arm for g in GROUPS}}
    return [
        PlannedCall(
            page_id=page_id,
            group=group,
            repeat=repeat,
            fingerprint=fingerprints[group.arm],
            run_id=determinism_run_id(page_id, group, repeat, fingerprints[group.arm], model),
        )
        for page_id in page_ids
        for group in GROUPS
        for repeat in range(REPEATS)
    ]


def _chosen_pages(paths: TryoutPaths) -> list[TryoutPage]:
    pages = read_jsonl(paths.pages, TryoutPage)
    if not pages:
        raise TryoutError(f"No page in {paths.pages}: run 'select' first.")
    return first_page_per_source(pages)


async def _execute(llm: TryoutLLM, call: PlannedCall, text: str) -> DeterminismRun:
    group = call.group
    record = DeterminismRun(
        run_id=call.run_id,
        page_id=call.page_id,
        arm=group.arm,
        temperature=group.temperature,
        repeat=call.repeat,
        model=llm.model,
        prompt_version=V2_PROMPT_VERSION if group.arm == "v2" else PLAIN_PROMPT_VERSION,
        prompt_fingerprint=call.fingerprint,
        settings=group.settings,
        created_at=datetime.now(UTC),
    )
    v2_update: dict[str, object] = {}
    try:
        if group.arm == "v2":
            v2 = await run_v2(llm, text)
            output = v2.rendered_text
            v2_update = {"outcome": v2.outcome, "retried": v2.retried}
        elif group.temperature == PLAIN_TEMPERATURE:
            output = (await run_plain(llm, text)).text
        else:
            prompt = build_plain_prompt(text)
            output = (await llm.generate(prompt, temperature=group.temperature)).text
    except Exception as exc:  # a technical error must not abort the batch
        return record.model_copy(update={"error": repr(exc)})
    return record.model_copy(
        update={
            **v2_update,
            "output_sha256": sha256_text(output) if output is not None else None,
            "n_output_chars": len(output) if output is not None else None,
        }
    )


async def run_determinism(
    paths: TryoutPaths,
    llm: TryoutLLM,
    *,
    on_record: Callable[[DeterminismRun], None] = lambda record: None,
) -> list[DeterminismRun]:
    """Refuses unless both prompts are the frozen ones (ADR-0013, point 10), then checks every
    page text, all before the first call. Calls are sequential; each record is appended as soon
    as its call ends."""
    mismatches = frozen_prompt_mismatches()
    if mismatches:
        raise TryoutError("\n".join(mismatches))
    pages = _chosen_pages(paths)
    texts = {page.page_id: cap_text(load_page_text(paths, page))[0] for page in pages}
    previous = latest_by(read_jsonl(paths.determinism, DeterminismRun), lambda r: r.run_id)
    done = {run_id for run_id, r in previous.items() if r.error is None}

    records: list[DeterminismRun] = []
    for call in plan_calls(list(texts), llm.model):
        if call.run_id in done:
            continue
        record = await _execute(llm, call, texts[call.page_id])
        append_jsonl(paths.determinism, [record])
        on_record(record)
        records.append(record)
    return records


# --- summary ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupStat:
    page_id: str
    group: Group
    done: int  # repeats recorded without technical error (a failed v2 generation is done)
    distinct: int  # distinct outputs; each failed v2 generation counts as its own value
    lengths: tuple[int, ...]

    @property
    def complete(self) -> bool:
        return self.done >= REPEATS


def group_stats(
    calls: Sequence[PlannedCall], latest: Mapping[str, DeterminismRun]
) -> list[GroupStat]:
    """One stat per (page, group), in call order, over the latest record of each call."""
    by_group: dict[tuple[str, Group], list[DeterminismRun]] = {}
    for call in calls:
        records = by_group.setdefault((call.page_id, call.group), [])
        record = latest.get(call.run_id)
        if record is not None and record.error is None:
            records.append(record)
    stats: list[GroupStat] = []
    for (page_id, group), records in by_group.items():
        # A failed v2 generation has no output to compare: it cannot be shown identical to
        # anything, so each one is a value of its own.
        values = {r.output_sha256 or f"failed:{r.run_id}" for r in records}
        lengths = tuple(r.n_output_chars for r in records if r.n_output_chars is not None)
        stats.append(GroupStat(page_id, group, len(records), len(values), lengths))
    return stats


def stat_line(stat: GroupStat) -> str:
    length = f"{min(stat.lengths)}-{max(stat.lengths)}" if stat.lengths else "-"
    line = (
        f"{stat.page_id} {stat.group.label}: {stat.done}/{REPEATS} repeats, "
        f"{stat.distinct} distinct, length {length}"
    )
    return line if stat.complete else f"{line} (incomplete)"


def summary_lines(stats: Sequence[GroupStat]) -> list[str]:
    """One line per (arm, temperature): identical outputs on every page, or not."""
    lines: list[str] = []
    for group in GROUPS:
        of_group = [s for s in stats if s.group == group]
        incomplete = [s.page_id for s in of_group if not s.complete]
        if incomplete:
            lines.append(f"{group.label}: incomplete ({', '.join(incomplete)})")
            continue
        different = [s.page_id for s in of_group if s.distinct != 1]
        if different:
            lines.append(
                f"{group.label}: different outputs on {len(different)} of {len(of_group)} pages "
                f"({', '.join(different)})"
            )
        else:
            lines.append(f"{group.label}: identical outputs on every page ({len(of_group)} pages)")
    return lines


def determinism_report(paths: TryoutPaths, model: str) -> list[str]:
    """The per-group lines then the summary lines, for the calls planned with `model`."""
    calls = plan_calls([p.page_id for p in _chosen_pages(paths)], model)
    latest = latest_by(read_jsonl(paths.determinism, DeterminismRun), lambda r: r.run_id)
    stats = group_stats(calls, latest)
    return [*(stat_line(s) for s in stats), *summary_lines(stats)]


def call_line(record: DeterminismRun) -> str:
    """One line per executed call: counters only, no model text."""
    head = f"{record.page_id} {record.arm:<5} t={record.temperature:g} #{record.repeat}"
    if record.error:
        return f"{head} ERROR {record.error[:200]}"
    return f"{head} {record.outcome or 'ok'}, {record.n_output_chars} chars"


async def cmd_determinism(paths: TryoutPaths, llm: TryoutLLM) -> None:
    pages = [p.page_id for p in _chosen_pages(paths)]
    print(f"Model {llm.model}, pages {', '.join(pages)}, {REPEATS} repeats per arm and temperature")
    records = await run_determinism(paths, llm, on_record=lambda r: print(call_line(r)))
    errors = sum(r.error is not None for r in records)
    print(f"Executed {len(records)} calls, {errors} errors (re-run the step to retry them).")
    for line in determinism_report(paths, llm.model):
        print(line)
