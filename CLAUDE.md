# Commands
- Install: `uv sync`
- Tests: `uv run pytest -q` | single: `uv run pytest <path> -q --tb=short`
- Types & lint: `uv run mypy && uv run ruff check .`
- Baseline v1 eval: `uv run python -m evals.baseline_v1 --help`
- Local stack (from week 1, needs Docker): `docker compose --profile dev up -d`

# Architecture invariants
- The LLM never produces URLs, ids or trust scores: it cites ids it was given; code resolves and validates them.
- External content (web, social, transcripts) is untrusted: wrap it in delimiters, never let it trigger write tools.
- Any write tool (WordPress) requires a persisted APPROVED workflow state.
- Tools live in `src/dzdia_newsroom/tools/`; Telegram and MCP are thin adapters.
- Webhooks ack immediately; work goes through the Postgres job queue.
- No agent framework: the agent loop is hand-written in `agent/loop.py`.
- Copyrighted article text and raw model outputs stay in `evals/.data/` (gitignored).

# Engineering standards
- Python 3.12+, mypy strict, Pydantic v2 idioms, async I/O (httpx, google-genai `client.aio`).
- Every external call has a timeout, bounded retries with backoff, and is logged with cost/latency.
- Prompts are versioned; changing one requires running the related eval suite.

# Mentorship rules
- Explain in French; code, identifiers, comments and docstrings in English.
- Before a new module: ask me 3-4 architecture questions, then record the decision in `docs/architecture_decisions.md`.
- For core functions: present 2-3 options with trade-offs and let me choose before writing.
- Do not write the kata topics for me (agent loop core, RRF, structured-output retry, recall@k/MRR, kappa, SSRF guard) unless I ask for the comparison.
- When debugging: list root causes by probability and wait for my go before editing.

# Read only when needed
- `docs/SPEC.md`: active feature spec.
- `docs/architecture_decisions.md`: past decisions.
