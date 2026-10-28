# Getting started

This page takes a new contributor from a fresh clone to running tests and processes locally. For the full Docker stack, see [Deployment](../operations/deployment.md); for tests in detail, see [Testing](testing.md).

## Prerequisites

- Python 3.12 or later (`requires-python = ">=3.12"`).
- [uv](https://docs.astral.sh/uv/), which manages the virtual environment and the lock file (`uv.lock`).
- Docker Desktop, for Postgres and the integration tests. On Windows, install WSL 2 as well: Docker Desktop runs Linux containers through it.

## Install

```bash
uv sync
cp .env.example .env
```

Then fill `.env`. The variables are described in the [configuration reference](../operations/configuration.md). `.env` is gitignored and excluded from the Docker build context; never commit it.

Check the setup:

```bash
uv run pytest -q && uv run mypy && uv run ruff check .
```

Without `TEST_DATABASE_URL`, the Postgres integration tests are skipped.

## Run the processes locally

The processes need a reachable Postgres. The simplest option is the compose service, whose port is bound to `127.0.0.1`:

```bash
docker compose up -d postgres
```

Then, with `DATABASE_URL` pointing to it (for example `postgresql://dzdia:<POSTGRES_PASSWORD>@127.0.0.1:5432/dzdia` in `.env`):

```bash
uv run python -m dzdia_newsroom.db.migrate   # apply pending migrations
uv run python -m dzdia_newsroom.api          # webhook receiver on 0.0.0.0:8000 (--host, --port)
uv run python -m dzdia_newsroom.worker       # job worker
```

Set `LOG_FORMAT=text` for readable logs. The API needs `TELEGRAM_WEBHOOK_SECRET`; the worker needs `TELEGRAM_BOT_TOKEN`. To receive real Telegram updates, you need a public HTTPS URL: use the `dev` compose profile and its `cloudflared` tunnel ([Deployment](../operations/deployment.md#local-stack-profile-dev)).

### Windows note

psycopg's async mode does not work on Windows' default `ProactorEventLoop`. The entry points of `api`, `worker` and `db.migrate` pass `loop_factory=event_loop_factory()` from [`runtime.py`](../../src/dzdia_newsroom/runtime.py), which selects `asyncio.SelectorEventLoop` on Windows and the default loop elsewhere. Any new async entry point that uses psycopg must do the same. The pytest suite has the same limitation, which is why the integration tests run in a Linux container on Windows ([Testing](testing.md#running-integration-tests-on-windows)).

## Repository layout

```text
.
├── src/dzdia_newsroom/        application package
│   ├── api/                   FastAPI app: Telegram webhook and /health, entry point `python -m dzdia_newsroom.api`
│   ├── channels/              platform adapters: Channel protocol, InboundMessage, Telegram parser, client and CLI
│   ├── db/                    connection pool, migration runner, SQL migrations, job repository and row models
│   ├── worker/                job queue worker (lease, heartbeat, retries), handlers, entry point
│   ├── config.py              typed settings, one class per process need
│   ├── logging_setup.py       JSON log formatter and logging configuration
│   └── runtime.py             event loop selection (Windows)
├── evals/
│   ├── baseline_v1/           v1 hallucination baseline: sampling, snapshots, runs, extraction, matching, labels, report
│   ├── datasets/baseline_v1/  committed dataset files: feeds, sample, snapshot hashes (labels and report later)
│   └── .data/                 local only, gitignored: article snapshots and raw model outputs (copyrighted)
├── tests/                     pytest suite, mirroring the package layout (tests/db: Postgres integration tests)
├── docs/                      architecture, ADRs, operations and development documentation
├── Dockerfile                 two-stage image shared by api, worker, migrate and telegram-cli
├── docker-compose.yml         services and profiles (dev, prod, tools)
├── Caddyfile                  production reverse proxy configuration
└── pyproject.toml             dependencies and tool configuration (ruff, mypy, pytest)
```
