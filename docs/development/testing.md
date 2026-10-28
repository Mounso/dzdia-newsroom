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
| `tests/test_supply_chain.py` | static | Every image and action is pinned; CI, `Dockerfile` and compose agree (see [conventions](conventions.md#dependencies-and-supply-chain)) |
| `tests/test_deploy_guards.py` | static | Only `caddy` publishes ports outside `127.0.0.1`; app services run the `dzdia-newsroom:local` image that `deploy.sh` retags; deployment scripts are strict (`set -euo pipefail`), LF-only and call `main` on their last line ([ADR-0007](../adr/0007-production-host-on-oracle-cloud.md)) |
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
   docker network create dzdia-test && docker run -d --name dzdia-test-pg --network dzdia-test -e POSTGRES_USER=dzdia -e POSTGRES_PASSWORD=dzdia -e POSTGRES_DB=dzdia_test pgvector/pgvector:0.8.7-pg16-bookworm
   ```

2. Run the suite in a uv container, with the repository mounted read-only:

   ```bash
   docker run --rm --network dzdia-test -v "<repo>:/app:ro" -w /app -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_CACHE_DIR=/tmp/uvcache -e TEST_DATABASE_URL=postgresql://dzdia:dzdia@dzdia-test-pg:5432/dzdia_test ghcr.io/astral-sh/uv:0.13.6-python3.12-trixie-slim sh -c "uv sync --frozen --quiet && uv run --frozen pytest -q -p no:cacheprovider"
   ```

3. Clean up:

   ```bash
   docker rm -f dzdia-test-pg && docker network rm dzdia-test
   ```

On Linux and macOS, start any Postgres 16 with pgvector and export `TEST_DATABASE_URL` before `uv run pytest -q`.

## Continuous integration

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs on every push and pull request, and every Monday for the audit only. The workflow token is read-only (`permissions: contents: read`), checkouts do not keep it, every job has a timeout, and a new push cancels the previous run of the same branch except on `main`. Three jobs run in parallel:

| Job | Steps |
|---|---|
| `checks` | With a Postgres service (same image as compose) and `TEST_DATABASE_URL` set: `uv sync --frozen` (fails if `uv.lock` is out of date), `ruff check`, `ruff format --check`, `mypy`, `shellcheck -x deploy/*.sh` (preinstalled on the runner), then `pytest --cov`, integration tests included. Fails under the coverage floor; the coverage table is written to the job summary. |
| `audit` | `pip-audit` on every group of `uv.lock` (see [conventions](conventions.md#dependencies-and-supply-chain)). |
| `image` | Builds the image for `linux/amd64` and `linux/arm64` (QEMU emulation; the production VM is arm64) with the GitHub Actions cache, then loads the amd64 image and imports every entry point as the non-root user: the runtime venv has no dev dependencies, so a package that only the tests provided shows up here. Nothing is pushed. |

### Deployment scripts

The bash scripts in [`deploy/`](../../deploy/) are checked statically in the CI (shellcheck and the guard tests above). Locally, without a Windows shellcheck:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "<repo>:/mnt:ro" -w /mnt koalaman/shellcheck:stable -x deploy/*.sh
```

Their behavior was tested on 2026-10-28 in Linux containers, not in the CI:

- `backup.sh --keep-local` against a Postgres with the real migration, followed by `restore-drill.sh` with the right key (tables and counts restored) and with a wrong key (refused). The encrypted file contained no plain text;
- `deploy.sh` as a non-root member of the Docker socket's group: refusal of root, of a ref outside `origin/main` and of a dirty checkout; `--dry-run` of a deployment; rollback targets through a history with failed and reverted entries;
- `bootstrap.sh`'s firewall step in Ubuntu 24.04 with Oracle-shaped `iptables` rules: ports 443 and 80 inserted after SSH and before the `REJECT`, live and in `rules.v4`, a Docker chain left intact, and no change on a second run. Its SSH step refuses to proceed when no ed25519 key is authorized.

Uploads to Object Storage, `sshd`, systemd and `unattended-upgrades` can only be tested on the VM.

### Coverage

Branch coverage of `src/dzdia_newsroom` and `evals`, configured in `pyproject.toml` (`[tool.coverage.*]`). `fail_under` is a ratchet, one point under the measured total (69 % on 2026-10-28, floor 68): the margin absorbs rounding, not regressions. Raise it when coverage grows, never lower it to make a change pass. Plain `uv run pytest` does not measure coverage. On Windows, `uv run pytest --cov` skips the Postgres tests and falls under the floor (about 64 %), which is expected; add `--cov` to the Docker procedure above for the real figure.

## Principles

- **Deterministic time and randomness.** Code that sleeps or draws jitter takes the function as a parameter (`sleep=asyncio.sleep`, `rand=random.random` in `TelegramClient`, `Worker` and `retry_delay_s`). Tests inject instant sleeps and fixed values, so retry and backoff behavior is asserted exactly, without waiting.
- **Protocols instead of mocks.** Dependencies are typed as `Protocol`s (`JobStore`, `JobEnqueuer`, `Channel`, `TextGenerator`, `StructuredGenerator`). Tests pass small fakes that satisfy them, and mypy checks that the fakes match.
- **No network in unit tests.** HTTP clients are given an `httpx.AsyncClient` built on `httpx.MockTransport`; the FastAPI app is created with an in-memory enqueuer. Only `tests/db/` talks to a real service, and only to Postgres.
- **Real Postgres for SQL.** Queue guarantees that depend on Postgres semantics (`SKIP LOCKED`, lease expiry, fencing tokens, `NOTIFY`) are tested against Postgres itself, never against an imitation.
- **Tests name the behavior.** Test names state the expected outcome (`test_expired_lease_is_reclaimed_and_old_token_fenced`), so a failure reads as a broken guarantee.
