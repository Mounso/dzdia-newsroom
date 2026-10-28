# Testing

This page describes how the test suite is organized, how to run it (including the Postgres integration tests on Windows), what the CI checks, and the principles the tests follow.

## Layout

| Path | Kind | What it covers |
|---|---|---|
| `tests/api/` | unit | Webhook: secret check, allowlist, status codes, deduplication, no exposed docs |
| `tests/channels/` | unit | Telegram update parsing; Bot API client retries, `retry_after`, error classes, token redaction |
| `tests/worker/` | unit | Queue worker (completion, retries, dead jobs, heartbeat, lost lease, graceful stop) and handlers |
| `tests/evals/` | unit | Baseline harness: sampling, snapshots, runner, extraction, matcher, labeling, report, LLM retry |
| `tests/test_config.py`, `tests/test_logging_setup.py` | unit | Settings parsing and validation; JSON log format |
| `tests/db/` | integration | Migrations, `JobRepository` against a real Postgres, end-to-end chain webhook -> `jobs` -> worker -> reply |

Shared test doubles:

- `FakeStore` in `tests/worker/test_queue.py`: an in-memory `JobStore` that records every call the worker makes.
- `FakeChannel` in `tests/worker/test_handlers.py`: records the messages a handler sends.
- `FakeJobs` in `tests/api/test_webhook.py`: an in-memory `JobEnqueuer` for the FastAPI app.
- `FakeTextGenerator` and `FakeStructuredGenerator` in `tests/evals/fakes.py`: LLM generators returning canned outputs.
- `tests/telegram_updates.py`: builders for Telegram update payloads.

## Commands

```bash
uv run pytest -q                                   # whole suite
uv run pytest tests/worker/test_queue.py -q --tb=short   # one file
uv run pytest "tests/worker/test_queue.py::test_unknown_kind_is_dead" -q   # one test
uv run mypy                                        # strict type checking of src, evals and tests
uv run ruff check .                                # lint
uv run ruff format --check .                       # formatting
```

`asyncio_mode = "auto"` is set in `pyproject.toml`: async test functions need no decorator.

## Integration tests

The tests in `tests/db/` run only when `TEST_DATABASE_URL` is set; otherwise they are skipped. Each test creates its own schema with a random name, sets it as the connection's `search_path`, applies the migrations in it, and drops it at the end (`tests/db/conftest.py`). The target database is never modified outside that schema, so the same database can serve repeated runs.

### Running integration tests on Windows

psycopg's async mode does not work on the default Windows event loop, so on Windows the whole suite runs in Linux containers. From Git Bash, prefix the commands with `MSYS_NO_PATHCONV=1` so that paths are not rewritten.

1. Start a throwaway Postgres on a dedicated network:

   ```bash
   docker network create dzdia-test && docker run -d --name dzdia-test-pg --network dzdia-test -e POSTGRES_USER=dzdia -e POSTGRES_PASSWORD=dzdia -e POSTGRES_DB=dzdia_test pgvector/pgvector:pg16
   ```

2. Run the suite in a uv container, with the repository mounted read-only:

   ```bash
   docker run --rm --network dzdia-test -v "<repo>:/app:ro" -w /app -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uvcache -e TEST_DATABASE_URL=postgresql://dzdia:dzdia@dzdia-test-pg:5432/dzdia_test ghcr.io/astral-sh/uv:python3.12-bookworm-slim sh -c "uv sync --frozen --quiet && uv run --frozen pytest -q -p no:cacheprovider"
   ```

3. Clean up:

   ```bash
   docker rm -f dzdia-test-pg && docker network rm dzdia-test
   ```

On Linux and macOS, start any Postgres 16 with pgvector and export `TEST_DATABASE_URL` before `uv run pytest -q`.

## Continuous integration

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs on every push and pull request, on `ubuntu-latest`, with a `pgvector/pgvector:pg16` service container and `TEST_DATABASE_URL` set. Steps, in order:

1. `uv sync --frozen` (fails if `uv.lock` is out of date);
2. `uv run ruff check .`;
3. `uv run ruff format --check .`;
4. `uv run mypy`;
5. `uv run pytest -q`, integration tests included.

## Principles

- **Deterministic time and randomness.** Code that sleeps or draws jitter takes the function as a parameter (`sleep=asyncio.sleep`, `rand=random.random` in `TelegramClient`, `Worker` and `retry_delay_s`). Tests inject instant sleeps and fixed values, so retry and backoff behavior is asserted exactly, without waiting.
- **Protocols instead of mocks.** Dependencies are typed as `Protocol`s (`JobStore`, `JobEnqueuer`, `Channel`, `TextGenerator`, `StructuredGenerator`). Tests pass small fakes that satisfy them, and mypy checks that the fakes match.
- **No network in unit tests.** HTTP clients are given an `httpx.AsyncClient` built on `httpx.MockTransport`; the FastAPI app is created with an in-memory enqueuer. Only `tests/db/` talks to a real service, and only to Postgres.
- **Real Postgres for SQL.** Queue guarantees that depend on Postgres semantics (`SKIP LOCKED`, lease expiry, fencing tokens, `NOTIFY`) are tested against Postgres itself, never against an imitation.
- **Tests name the behavior.** Test names state the expected outcome (`test_expired_lease_is_reclaimed_and_old_token_fenced`), so a failure reads as a broken guarantee.
