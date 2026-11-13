"""CLI of the v2 comparison (ADR-0013). Run `python -m evals.v2.compare <step> --help`.

Steps:
- `run`: the `v2` and `plain_prompt` arms on the article task, once per non-opaque sample item;
  idempotent, prints counters only.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import cast

from dzdia_newsroom.llm import GeminiClient
from evals.v2.compare import CompareError
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

    args = parser.parse_args(argv)
    paths = ComparePaths.default()
    try:
        match args.step:
            case "run":
                conditions = cast(list[CompareCondition], args.conditions)
                asyncio.run(cmd_run(paths, conditions, args.items))
    except CompareError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
