"""CLI of the v2 comparison (ADR-0013). Run `python -m evals.v2.compare <step> --help`.

Steps:
- `run`: the `v2` and `plain_prompt` arms on the article task, once per non-opaque sample item;
  idempotent, prints counters only.
- `extract`: the baseline's extractor on the plain outputs and the rendered v2 articles (a failed
  v2 generation is not extracted); refuses an extractor other than the baseline's; idempotent,
  an extraction error is retried by running the step again.
- `match`: the baseline's matcher against the snapshot texts; prints the number matched only.
- `export-labels`: the blind sheet (key, item, type, value, context, label, note) of the elements
  not labeled yet, both new arms and the unlabeled `page_text` elements, shuffled by key inside
  each article; refuses while runs, extractions or matches are missing, or while the sheet holds
  labels not imported.
- `import-labels`: appends the filled rows of the sheet to the comparison's labels; blank rows
  stay pending; any unknown key or invalid label stops the import before anything is written.
- `check-labels`: three mechanical checks on the labels, listed by article and key.

No step prints model text, element values, element ids, arms or labels per row.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import cast

from dzdia_newsroom.llm import GeminiClient
from evals.v2.compare import CompareError
from evals.v2.compare.extract import (
    check_extractor,
    extract_compare,
    extract_lines,
    match_compare,
    pending_extractions,
)
from evals.v2.compare.labels import check_labels, check_lines, export_labels, import_labels
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.run import (
    COMPARE_CONDITIONS,
    CompareCondition,
    ordered,
    run_compare,
    run_line,
    summarize,
    summary_lines,
)


def _gemini() -> GeminiClient:
    from dzdia_newsroom.config import get_llm_settings

    settings = get_llm_settings()
    return GeminiClient(
        settings.gemini_api_key.get_secret_value(),
        model=settings.generation_model,
        max_concurrency=settings.llm_max_concurrency,
        max_rpm=settings.llm_max_rpm,
        timeout_s=settings.llm_timeout_s,
    )


def _extractor() -> GeminiClient:
    """The baseline's extractor client (`extraction_model`, `extraction_thinking_level`)."""
    from dzdia_newsroom.config import get_llm_settings

    settings = get_llm_settings()
    return GeminiClient(
        settings.gemini_api_key.get_secret_value(),
        model=settings.extraction_model,
        max_concurrency=settings.llm_max_concurrency,
        max_rpm=settings.llm_max_rpm,
        timeout_s=settings.llm_timeout_s,
        thinking_level=settings.extraction_thinking_level,
    )


async def cmd_run(
    paths: ComparePaths, conditions: Sequence[CompareCondition], item_ids: Sequence[str]
) -> None:
    llm = _gemini()
    print(f"Model {llm.model}, conditions {', '.join(ordered(conditions))}")
    records = await run_compare(
        paths,
        llm,
        conditions=conditions,
        item_ids=item_ids,
        on_record=lambda r: print(run_line(r)),
    )
    errors = sum(r.error is not None for r in records)
    print(f"Executed {len(records)} runs, {errors} technical errors (re-run the step to retry).")
    for line in summary_lines(summarize(paths, conditions, item_ids)):
        print(line)


async def cmd_extract(paths: ComparePaths) -> None:
    generator = _extractor()
    check_extractor(paths, generator.model, generator.thinking_level)
    todo = len(pending_extractions(paths))
    print(f"Extracting {todo} runs with {generator.model} ({generator.thinking_level})")
    for line in extract_lines(await extract_compare(paths, generator)):
        print(line)


def cmd_match(paths: ComparePaths) -> None:
    print(f"Matched {match_compare(paths)} elements.")


def cmd_export_labels(paths: ComparePaths) -> None:
    record = export_labels(paths)
    print(f"{record.n_rows} rows to label -> {paths.label_sheet}")


def cmd_import_labels(paths: ComparePaths) -> None:
    s = import_labels(paths)
    print(
        f"Appended {s.appended} labels, of which {s.changed} changed; "
        f"{s.labeled} labeled, {s.pending} pending in the population."
    )


def cmd_check_labels(paths: ComparePaths) -> None:
    for line in check_lines(*check_labels(paths)):
        print(line)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evals.v2.compare",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    steps = parser.add_subparsers(dest="step", required=True)
    run = steps.add_parser("run", help="Run the v2 and plain_prompt arms on the sample articles")
    run.add_argument(
        "--conditions", nargs="+", choices=COMPARE_CONDITIONS, default=list(COMPARE_CONDITIONS)
    )
    run.add_argument("--items", nargs="+", default=[], metavar="ITEM_ID")
    steps.add_parser("extract", help="Extract the critical elements of the outputs (LLM)")
    steps.add_parser("match", help="Look for each element in the snapshot text (code)")
    steps.add_parser("export-labels", help="Write the blind CSV sheet of elements to label")
    steps.add_parser("import-labels", help="Import the filled rows of the sheet")
    steps.add_parser("check-labels", help="Run the three mechanical checks on the labels")

    args = parser.parse_args(argv)
    paths = ComparePaths.default()
    try:
        match args.step:
            case "run":
                conditions = cast(list[CompareCondition], args.conditions)
                asyncio.run(cmd_run(paths, conditions, args.items))
            case "extract":
                asyncio.run(cmd_extract(paths))
            case "match":
                cmd_match(paths)
            case "export-labels":
                cmd_export_labels(paths)
            case "import-labels":
                cmd_import_labels(paths)
            case "check-labels":
                cmd_check_labels(paths)
    except CompareError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
