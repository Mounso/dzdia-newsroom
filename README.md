# Dzdia Newsroom Engine

AI assistant for the editorial team of [dzdia.com](https://dzdia.com): daily morning briefing (web + social) and article drafting from a link, an audio or a video, with grounded citations and human approval before any WordPress export.

> Work in progress: rewrite of the legacy `dzdia-bot` prototype. Specification: `Refactorisation DzdiaBot v2.1.md`; decisions: [`docs/architecture_decisions.md`](docs/architecture_decisions.md).

## Setup
```bash
uv sync
cp .env.example .env   # then set GEMINI_API_KEY
uv run pytest -q && uv run mypy && uv run ruff check .
```

## Baseline v1 hallucination eval (ADR-001)
Measures how often the legacy bot states names, numbers, dates or quotes that are not in the source article.

```bash
uv run python -m evals.baseline_v1 sample          # freeze 16 RSS articles + 4 opaque-URL duplicates
uv run python -m evals.baseline_v1 snapshot        # store HTML + extracted text (local only)
# review evals/.data/baseline_v1/snapshots/*.txt by hand, then:
uv run python -m evals.baseline_v1 review-done <item_id> ...
uv run python -m evals.baseline_v1 run             # v1 prompts: url_only and page_text conditions
uv run python -m evals.baseline_v1 extract         # critical elements (LLM, structured output)
uv run python -m evals.baseline_v1 match           # deterministic presence check (code)
uv run python -m evals.baseline_v1 export-labels   # CSV: label S / C / N
uv run python -m evals.baseline_v1 import-labels
uv run python -m evals.baseline_v1 report          # rates with cluster-bootstrap 95 % CI
```

Article snapshots and raw model outputs stay in `evals/.data/` (gitignored, copyrighted content).
