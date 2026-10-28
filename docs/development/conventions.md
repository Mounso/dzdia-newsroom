# Conventions

This page collects the rules every change follows: architecture invariants, engineering standards, logging, error handling, and the step-by-step procedures for common extensions (job kinds, migrations, environment variables, ADRs, prompts, commits).

## Architecture invariants

These hold for all code, present and future. A change that breaks one needs an ADR first.

- **The LLM never produces URLs, ids or trust scores.** The model cites ids it was given; code resolves and validates them. A hallucinated link or source id can then never reach a journalist.
- **External content is untrusted.** Web pages, social posts and transcripts are wrapped in delimiters in prompts and can never trigger a write tool. This is the main defense against prompt injection.
- **Any write tool (WordPress) requires a persisted `APPROVED` workflow state.** Approval is a database fact checked by code, not a model decision.
- **Tools live in `src/dzdia_newsroom/tools/`; Telegram and MCP are thin adapters.** Business logic does not depend on the channel it is called from.
- **Webhooks acknowledge immediately; work goes through the Postgres job queue.** Telegram gets its answer in milliseconds, and slow work survives restarts.
- **No agent framework.** The agent loop is hand-written in `agent/loop.py`, so every step is visible, typed and testable.
- **Copyrighted article text and raw model outputs stay in `evals/.data/`.** That directory is gitignored and excluded from the Docker build context.

The `tools/` and `agent/` packages and the workflow state do not exist yet: they arrive with the week 2 features.

## Engineering standards

- **Python 3.12+, mypy strict, Pydantic v2.** `uv run mypy` checks `src`, `evals` and `tests` with `strict = true` and the Pydantic plugin. Settings use `pydantic-settings`; row and message models are Pydantic models.
- **Async I/O.** HTTP goes through `httpx.AsyncClient`, Postgres through psycopg 3 async, Gemini through `client.aio`.
- **Every external call has a timeout, bounded retries with backoff, and is logged with latency (and cost for LLM calls).** Example: `TelegramClient._call` retries network errors, 5xx and short 429s up to 3 attempts with jittered exponential backoff, and logs `method`, `status`, `attempt` and `latency_ms` for each call.
- **Prompts are versioned.** Each prompt has a version string recorded with its outputs (`PROMPT_VERSION = "v1-frozen"`, `EXTRACTION_PROMPT_VERSION = "extract-v1"`). Changing a prompt means bumping its version and running the related eval suite before merging.
- **Formatting and lint.** `ruff format` (line length 100) and `ruff check` with the `E, F, I, UP, B, SIM, RUF` rule sets. The CI fails on any finding.
- **Language.** Code, identifiers, comments, docstrings and documentation are in English. User-facing bot messages are in French.

## Logging

- One JSON object per line ([`logging_setup.py`](../../src/dzdia_newsroom/logging_setup.py)), with the fixed fields `ts`, `level`, `logger` and `msg`.
- Context goes in `extra`, so it becomes a filterable field:

  ```python
  logger.info("update queued", extra={"update_id": update_id, "job_id": job_id})
  ```

- Never log secrets or message text. Bot API URLs contain the token, which is why the `httpx` and `httpcore` loggers are raised to `WARNING`, and why `TelegramClient` redacts the token from error messages.
- Error messages stored in `jobs.last_error` follow the same rule: for example, an invalid payload is reported by its error count only, because Pydantic's message would copy user text.
- Entry points run as `python -m` use an explicit logger name (`logging.getLogger("dzdia_newsroom.worker")`), because `__name__` is `"__main__"` there.

## Error taxonomy

| Exception | Raised by | Meaning | Effect |
|---|---|---|---|
| `ChannelError` | channel adapters (`TelegramClient`) | Sending failed but may succeed later: network error, server error, rate limit too long to wait in place | The handler lets it propagate; the queue retries the job with backoff |
| `PermanentChannelError` (subclass of `ChannelError`) | channel adapters | The platform rejected the request (4xx other than 429): chat not found, bot blocked, invalid request | The handler converts it to `PermanentJobError` |
| `PermanentJobError` | job handlers | Retrying cannot help: invalid payload, refused input | The job goes straight to `dead`, without retry |
| any other `Exception` | job handlers | Presumed transient | Retried with full-jitter exponential backoff until `max_attempts`, then `dead` |

Short failures are retried inside the client (seconds); longer ones are retried by the queue (minutes). Classify an error as permanent only when a retry is certain to fail the same way.

## Adding a job kind

1. Choose a kind name and, if needed, a constant for it next to the payload model (see `INBOUND_MESSAGE_KIND` in [`channels/base.py`](../../src/dzdia_newsroom/channels/base.py)).
2. Write the handler in [`worker/handlers.py`](../../src/dzdia_newsroom/worker/handlers.py): an `async def handler(job: ClaimedJob) -> None` that validates `job.payload` with a Pydantic model, and register it in the mapping returned by `build_handlers`.
3. **Make it idempotent.** Delivery is at least once: a handler can run twice for the same job (a worker can die after the side effect and before `complete`). Either make the effect harmless to repeat, or record it before returning and check that record on the next run.
4. Raise `PermanentJobError` for errors a retry cannot fix; let everything else propagate.
5. Enqueue with `JobRepository.enqueue(kind, payload, dedup_key=...)`. Use a `dedup_key` whenever the trigger can be delivered twice.
6. Test the handler with a fake channel or store, without a database.

A job whose kind has no handler is marked `dead` (`no handler for kind ...`).

## Adding a migration

1. Create the next numbered file in [`src/dzdia_newsroom/db/migrations/`](../../src/dzdia_newsroom/db/migrations/), for example `0002_<description>.sql`. Files are applied in name order; the version recorded in `schema_migrations` is the file stem.
2. Write plain SQL. Each file runs in its own transaction, under an advisory lock that serializes concurrent runners.
3. **Never edit a migration that has been applied anywhere.** The runner skips known versions and does not compare contents, so an edit would silently diverge between databases. Write a new migration instead.
4. Apply it with `uv run python -m dzdia_newsroom.db.migrate` (local), or let the compose `migrate` service run it at the next deployment. Integration tests apply all migrations to their throwaway schema automatically.

## Adding an environment variable

1. Add the field to the settings class of the process that needs it in [`config.py`](../../src/dzdia_newsroom/config.py), or create a new class if no existing one fits: a process must not receive settings it does not use.
2. Pass it to that service only, in its `environment:` block in [`docker-compose.yml`](../../docker-compose.yml). Use `${VAR:?message}` if it is required, `${VAR:-}` or `${VAR:-default}` otherwise.
3. Add it to `.env.example` with a safe placeholder.
4. Document it in the [configuration reference](../operations/configuration.md).
5. Add a test in `tests/test_config.py` if it has parsing or validation logic.

## Dependencies and supply chain

Everything the build or the CI downloads is pinned, so that a build is reproducible and a moved tag cannot inject code (as when `tj-actions/changed-files` was compromised in March 2025).

| What | Pinned by | Updated by |
|---|---|---|
| Python packages | `uv.lock`, with hashes | Dependabot (`uv`), weekly |
| GitHub Actions | full commit SHA, version in a comment (`@<sha>  # v7.0.1`) | Dependabot (`github-actions`), weekly |
| Images in the `Dockerfile` and `docker-compose.yml` | exact tag and multi-arch index digest (`caddy:2.11.7@sha256:…`) | Dependabot (`docker`, `docker-compose`), weekly |
| Postgres service image and uv version in `ci.yml`, `# syntax=` line of the `Dockerfile` | same | by hand, in the Dependabot PR that bumps the matching reference |

- **Use the index digest**, not a platform digest: the image runs on amd64 (workstation, CI) and arm64 (the VM). `docker buildx imagetools inspect <image:tag>` prints it.
- **Guard test.** [`tests/test_supply_chain.py`](../../tests/test_supply_chain.py) fails on an unpinned image or action, and on drift that Dependabot cannot fix: the CI Postgres image must equal the compose one, the uv version in `ci.yml` must equal the `Dockerfile` uv stage, and both `Dockerfile` stages must use the same Python image.
- **Deliberate upgrades only** for Python (3.12, ignored by Dependabot beyond patches) and Postgres (`pg16-bookworm`: a major upgrade needs a dump and restore, and a new glibc can change collation order). Changing either means changing the guard test and following the migration procedure.
- **Dependabot PRs:** one grouped PR per ecosystem for minor and patch updates, one PR per major. Read the changelog of majors; merge only when the CI is green.
- **Vulnerability audit.** The `audit` CI job runs `pip-audit` on every group of `uv.lock` (exported with hashes, nothing resolved), on every push and every Monday. It fails on any known advisory. To accept one, add its id to [`.github/pip-audit-ignore.txt`](../../.github/pip-audit-ignore.txt) with the reason and a review date; prefer upgrading.

## Architecture decision records

Significant decisions (a new module, a dependency, a data model, an operational choice) are recorded as ADRs in [`docs/adr/`](../adr/README.md): context, decision, rejected alternatives, consequences. An accepted ADR is never rewritten; a later ADR supersedes or amends it and says so in its header.

## Commits

- Imperative subject line in English, describing the change: `Add Postgres job queue with heartbeat-renewed leases (ADR-004)`.
- One logical change per commit, with its tests and documentation.
- Reference the ADR in the subject when the commit implements one.
- The CI must pass: ruff, ruff format, mypy, the full test suite with its coverage floor, the dependency audit and the image build.
