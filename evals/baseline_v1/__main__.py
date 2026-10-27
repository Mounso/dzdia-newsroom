"""CLI of the v1 hallucination baseline. Run `python -m evals.baseline_v1 <step> --help`."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import cast, get_args

import httpx

from evals.baseline_v1.extract import extract_run
from evals.baseline_v1.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from evals.baseline_v1.labeling import read_label_sheet, select_for_labeling, write_label_sheet
from evals.baseline_v1.llm import GeminiClient
from evals.baseline_v1.matcher import DEFAULT_NAME_THRESHOLD, SourceIndex, match_element
from evals.baseline_v1.paths import Paths
from evals.baseline_v1.report import build_report
from evals.baseline_v1.runner import execute_jobs, plan_jobs
from evals.baseline_v1.sample import (
    FeedUnavailableError,
    build_sample,
    fetch_feeds,
    load_sampling_config,
)
from evals.baseline_v1.schemas import (
    Condition,
    ElementRecord,
    ExtractionRecord,
    HumanLabel,
    RunRecord,
    SampleItem,
    SnapshotMeta,
)
from evals.baseline_v1.snapshot import (
    load_snapshot_text,
    mark_reviewed,
    reextract,
    resolve_final_url,
    snapshot_item,
)

HTTP_TIMEOUT = httpx.Timeout(30.0)


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True, max_redirects=5)


def _gemini(model_attr: str) -> GeminiClient:
    from dzdia_newsroom.config import get_settings

    settings = get_settings()
    return GeminiClient(
        settings.gemini_api_key.get_secret_value(),
        model=getattr(settings, model_attr),
        max_concurrency=settings.llm_max_concurrency,
    )


async def cmd_sample(paths: Paths, force: bool) -> None:
    if paths.sample.exists() and not force:
        sys.exit(f"{paths.sample} exists: the sample is frozen. Use --force to rebuild it.")
    config = load_sampling_config(paths.feeds)
    try:
        async with _http_client() as client:
            entries = await fetch_feeds(config, client)
        items = build_sample(entries, config)
    except (FeedUnavailableError, ValueError) as exc:
        sys.exit(f"Sampling failed: {exc}\nEdit {paths.feeds} (and record the change in an ADR).")
    write_jsonl(paths.sample, items)
    print(f"Sample written: {len(items)} items -> {paths.sample}")


async def cmd_snapshot(paths: Paths) -> None:
    items = read_jsonl(paths.sample, SampleItem)
    metas = latest_by(read_jsonl(paths.snapshots_meta, SnapshotMeta), lambda m: m.snapshot_id)
    async with _http_client() as client:
        for item in items:
            if item.is_opaque or item.item_id in metas:
                continue
            try:
                meta = await snapshot_item(client, item, paths.snapshot_dir)
            except Exception as exc:
                print(f"FAILED {item.item_id} ({item.url}): {exc!r}")
                continue
            append_jsonl(paths.snapshots_meta, [meta])
            metas[meta.snapshot_id] = meta
            print(f"ok {item.item_id}: {meta.n_chars} chars")
        for item in items:
            if not item.is_opaque or item.snapshot_id not in metas:
                continue
            final_url = await resolve_final_url(client, item.url)
            expected = metas[item.snapshot_id].final_url
            status = "ok" if final_url == expected else f"MISMATCH (expected {expected})"
            print(f"{status} {item.item_id}: {item.url} -> {final_url}")


def cmd_reextract(paths: Paths) -> None:
    titles = {i.item_id: i.title for i in read_jsonl(paths.sample, SampleItem)}
    metas = latest_by(read_jsonl(paths.snapshots_meta, SnapshotMeta), lambda m: m.snapshot_id)
    for snapshot_id, meta in metas.items():
        updated = reextract(meta, paths.snapshot_dir, titles[snapshot_id])
        if updated.text_sha256 != meta.text_sha256:
            print(f"changed {snapshot_id}: {meta.n_chars} -> {updated.n_chars} chars")
        metas[snapshot_id] = updated if updated.text_sha256 != meta.text_sha256 else meta
    write_jsonl(paths.snapshots_meta, metas.values())


def cmd_review_done(paths: Paths, item_ids: Sequence[str]) -> None:
    metas = latest_by(read_jsonl(paths.snapshots_meta, SnapshotMeta), lambda m: m.snapshot_id)
    for item_id in item_ids:
        if item_id not in metas:
            sys.exit(f"No snapshot for {item_id}")
        metas[item_id] = mark_reviewed(metas[item_id], paths.snapshot_dir)
    write_jsonl(paths.snapshots_meta, metas.values())
    pending = sorted(m.snapshot_id for m in metas.values() if not m.reviewed)
    print(
        f"Reviewed: {len(metas) - len(pending)}/{len(metas)}. Pending: {', '.join(pending) or '-'}"
    )


async def cmd_run(paths: Paths, conditions: list[Condition], allow_unreviewed: bool) -> None:
    items = read_jsonl(paths.sample, SampleItem)
    metas = latest_by(read_jsonl(paths.snapshots_meta, SnapshotMeta), lambda m: m.snapshot_id)
    texts: dict[str, str] = {}
    if "page_text" in conditions:
        missing = [i.snapshot_id for i in items if i.snapshot_id not in metas]
        unreviewed = [m.snapshot_id for m in metas.values() if not m.reviewed]
        if missing:
            sys.exit(f"Missing snapshots: {sorted(set(missing))}")
        if unreviewed and not allow_unreviewed:
            sys.exit(f"Unreviewed snapshots: {unreviewed} (run review-done or --allow-unreviewed)")
        texts = {sid: load_snapshot_text(paths.snapshot_dir, sid) for sid in metas}

    jobs = plan_jobs(items, texts, conditions)
    previous = latest_by(read_jsonl(paths.runs, RunRecord), lambda r: r.run_id)
    done = {run_id for run_id, r in previous.items() if r.error is None}
    generator = _gemini("v1_model")
    already_done = len(done & {j.run_id for j in jobs})
    print(f"{len(jobs)} jobs, {already_done} already done, model {generator.model}")
    records = await execute_jobs(jobs, generator, done, lambda r: append_jsonl(paths.runs, [r]))
    errors = [r for r in records if r.error]
    print(f"Executed {len(records)} jobs, {len(errors)} errors (re-run the step to retry them).")
    for r in errors[:5]:
        print(f"  {r.run_id}: {r.error}")


async def cmd_extract(paths: Paths) -> None:
    runs = latest_by(read_jsonl(paths.runs, RunRecord), lambda r: r.run_id)
    previous = latest_by(read_jsonl(paths.extractions, ExtractionRecord), lambda x: x.run_id)
    todo = [
        r
        for r in runs.values()
        if r.error is None and (r.run_id not in previous or previous[r.run_id].error is not None)
    ]
    generator = _gemini("extraction_model")
    print(f"Extracting {len(todo)} runs with {generator.model}")
    elements = {e.element_id: e for e in read_jsonl(paths.elements, ElementRecord)}
    for next_done in asyncio.as_completed([extract_run(generator, r) for r in todo]):
        record, new_elements = await next_done
        append_jsonl(paths.extractions, [record])
        elements = {k: v for k, v in elements.items() if v.run_id != record.run_id}
        elements.update({e.element_id: e for e in new_elements})
        write_jsonl(paths.elements, elements.values())
        if record.error:
            print(f"  FAILED {record.run_id}: {record.error}")
    print(f"{len(elements)} elements in {paths.elements}")


def cmd_match(paths: Paths, name_threshold: float) -> None:
    items = {i.item_id: i for i in read_jsonl(paths.sample, SampleItem)}
    elements = read_jsonl(paths.elements, ElementRecord)
    indexes: dict[str, SourceIndex] = {}
    matched: list[ElementRecord] = []
    for e in elements:
        snapshot_id = items[e.item_id].snapshot_id
        if snapshot_id not in indexes:
            indexes[snapshot_id] = SourceIndex(load_snapshot_text(paths.snapshot_dir, snapshot_id))
        result = match_element(e.type, e.value, indexes[snapshot_id], name_threshold)
        matched.append(
            e.model_copy(
                update={
                    "match_status": "found" if result.found else "not_found",
                    "match_score": result.score,
                }
            )
        )
    write_jsonl(paths.elements, matched)
    found = sum(e.match_status == "found" for e in matched)
    print(f"Matched {len(matched)} elements: {found} found, {len(matched) - found} not found")


def cmd_export_labels(paths: Paths) -> None:
    elements = read_jsonl(paths.elements, ElementRecord)
    if any(e.match_status is None for e in elements):
        sys.exit("Some elements are not matched yet: run the 'match' step first.")
    labeled = {lab.element_id for lab in read_jsonl(paths.labels, HumanLabel)}
    count = write_label_sheet(paths.label_sheet, select_for_labeling(elements, labeled))
    print(f"{count} elements to label -> {paths.label_sheet}")
    print("Fill the 'label' column with S (SUPPORTED), C (CONTRADICTED) or N (NOT_IN_SOURCE),")
    print("judging against the reviewed snapshot text only, then run 'import-labels'.")


def cmd_import_labels(paths: Paths) -> None:
    labels = latest_by(read_jsonl(paths.labels, HumanLabel), lambda lab: lab.element_id)
    new = read_label_sheet(paths.label_sheet)
    labels.update({lab.element_id: lab for lab in new})
    write_jsonl(paths.labels, labels.values())
    print(f"Imported {len(new)} labels, {len(labels)} total -> {paths.labels}")


def cmd_report(paths: Paths) -> None:
    report = build_report(
        items=read_jsonl(paths.sample, SampleItem),
        runs=list(latest_by(read_jsonl(paths.runs, RunRecord), lambda r: r.run_id).values()),
        extractions=list(
            latest_by(read_jsonl(paths.extractions, ExtractionRecord), lambda x: x.run_id).values()
        ),
        elements=read_jsonl(paths.elements, ElementRecord),
        labels=latest_by(read_jsonl(paths.labels, HumanLabel), lambda lab: lab.element_id),
    )
    paths.report.write_text(report, encoding="utf-8")
    print(report)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.baseline_v1", description=__doc__)
    steps = parser.add_subparsers(dest="step", required=True)
    sample = steps.add_parser("sample", help="Build the frozen URL sample from the RSS feeds")
    sample.add_argument("--force", action="store_true", help="Overwrite an existing sample")
    steps.add_parser("snapshot", help="Download and extract each page; check opaque redirects")
    steps.add_parser("reextract", help="Rebuild snapshot texts from the stored HTML (no refetch)")
    review = steps.add_parser("review-done", help="Mark snapshot texts as reviewed by hand")
    review.add_argument("item_ids", nargs="+")
    run = steps.add_parser("run", help="Run the frozen v1 prompts")
    run.add_argument(
        "--conditions", nargs="+", choices=get_args(Condition), default=list(get_args(Condition))
    )
    run.add_argument("--allow-unreviewed", action="store_true")
    steps.add_parser("extract", help="Extract critical elements from the outputs (LLM)")
    match = steps.add_parser("match", help="Look for each element in the reference text (code)")
    match.add_argument("--name-threshold", type=float, default=DEFAULT_NAME_THRESHOLD)
    steps.add_parser("export-labels", help="Write the CSV sheet of elements to label")
    steps.add_parser("import-labels", help="Import the filled CSV sheet")
    steps.add_parser("report", help="Compute rates and write the markdown report")

    args = parser.parse_args(argv)
    paths = Paths.default()
    match args.step:
        case "sample":
            asyncio.run(cmd_sample(paths, args.force))
        case "snapshot":
            asyncio.run(cmd_snapshot(paths))
        case "reextract":
            cmd_reextract(paths)
        case "review-done":
            cmd_review_done(paths, args.item_ids)
        case "run":
            asyncio.run(
                cmd_run(paths, cast(list[Condition], args.conditions), args.allow_unreviewed)
            )
        case "extract":
            asyncio.run(cmd_extract(paths))
        case "match":
            cmd_match(paths, args.name_threshold)
        case "export-labels":
            cmd_export_labels(paths)
        case "import-labels":
            cmd_import_labels(paths)
        case "report":
            cmd_report(paths)


if __name__ == "__main__":
    main()
