"""CLI of the Dzdia corpus (ADR-0015). Run `python -m evals.dzdia_corpus <step> --help`.

Steps:
- `fetch`: every published post of the public REST API to `evals/.data/dzdia_corpus/posts/`;
  the first run lists all pages, a later run only the posts modified since the latest stored
  `modified_gmt`, `--full` all pages again. Rewrites the manifest and appends to the fetch log
  after a successful run; prints counters only.
- `stats`: counters over the local post files (lengths, paragraphs, headings, quoted passages,
  titles with quotation marks, italics, embeds, Instagram accounts cited); `--following` adds
  counts against a local CSV of followed accounts. Prints no title, text or account name.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from dzdia_newsroom.logging_setup import configure_logging
from evals.baseline_v1.sample import USER_AGENT
from evals.dzdia_corpus import CorpusError
from evals.dzdia_corpus.fetch import TIMEOUT_S, fetch_corpus, fetch_lines
from evals.dzdia_corpus.paths import CorpusPaths
from evals.dzdia_corpus.stats import compute, stats_lines


async def cmd_fetch(paths: CorpusPaths, full: bool) -> None:
    configure_logging("text", "INFO")  # one line per request; httpx's own lines are muted
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT_S), headers={"User-Agent": USER_AGENT}
    ) as client:
        run = await fetch_corpus(paths, client, full=full)
    for line in fetch_lines(run):
        print(line)


def cmd_stats(paths: CorpusPaths, following: Path | None) -> None:
    for line in stats_lines(compute(paths, following)):
        print(line)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m evals.dzdia_corpus",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    steps = parser.add_subparsers(dest="step", required=True)
    fetch = steps.add_parser("fetch", help="Copy the published posts locally (REST API)")
    fetch.add_argument("--full", action="store_true", help="List every page again")
    stats = steps.add_parser("stats", help="Print counters over the local posts")
    stats.add_argument(
        "--following",
        type=Path,
        metavar="CSV",
        help="Local CSV with a `username` column; only counts are printed from it",
    )

    args = parser.parse_args(argv)
    paths = CorpusPaths.default()
    try:
        match args.step:
            case "fetch":
                asyncio.run(cmd_fetch(paths, args.full))
            case "stats":
                cmd_stats(paths, args.following)
    except CorpusError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
