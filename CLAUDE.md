# Commands
- Install: `uv sync`
- Tests: `uv run pytest -q` | single: `uv run pytest <path> -q --tb=short`
- DB integration tests (`tests/db/`) run only when `TEST_DATABASE_URL` is set (CI sets it); each test uses a throwaway schema.
  On Windows, run them in Linux containers (psycopg async does not work on the default Windows event loop); from Git Bash, prefix with `MSYS_NO_PATHCONV=1`:
  1. `docker network create dzdia-test && docker run -d --name dzdia-test-pg --network dzdia-test -e POSTGRES_USER=dzdia -e POSTGRES_PASSWORD=dzdia -e POSTGRES_DB=dzdia_test pgvector/pgvector:pg16`
  2. `docker run --rm --network dzdia-test -v "<repo>:/app:ro" -w /app -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uvcache -e TEST_DATABASE_URL=postgresql://dzdia:dzdia@dzdia-test-pg:5432/dzdia_test ghcr.io/astral-sh/uv:python3.12-bookworm-slim sh -c "uv sync --frozen --quiet && uv run --frozen pytest -q -p no:cacheprovider"`
  3. `docker rm -f dzdia-test-pg && docker network rm dzdia-test`
- Migrations: `uv run python -m dzdia_newsroom.db.migrate` (needs `DATABASE_URL`)
- Types & lint: `uv run mypy && uv run ruff check .`
- Baseline v1 eval: `uv run python -m evals.baseline_v1 --help`
- Local stack (Docker, reads `.env`): `docker compose --profile dev up -d --build` (prod: `--profile prod`)
- Register the webhook: `docker compose run --rm telegram-cli set-webhook --url https://<host>/webhooks/telegram` (dev host: `docker compose logs cloudflared`)

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

# Project tracking
- Git: never add a `Co-Authored-By` trailer (or any Claude attribution) to commits or PRs.
- Start of session: read `docs/STATUS.md` (current state, paused work, next steps).
- End of each step: update `docs/STATUS.md` and `docs/fiche_de_suivi.md` (Abdou's interview-prep sheet, in French: journal entry, status table, concepts, likely recruiter questions, numbers, limits).
- Local-only files, never commit them (excluded in `.git/info/exclude`): `Refactorisation DzdiaBot v2.1.md` (full spec v2.1) and `docs/fiche_de_suivi.md`.

# Read only when needed
- `docs/SPEC.md`: active feature spec (not created yet).
- `Refactorisation DzdiaBot v2.1.md`: full product spec, target architecture and roadmap.
- `docs/architecture_decisions.md`: past decisions.
