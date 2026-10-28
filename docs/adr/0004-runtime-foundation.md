# ADR-0004: Runtime foundation: Telegram webhook, Postgres queue, worker and deployment

- **Status:** Accepted (except the HTTPS hostname, to be confirmed)
- **Date:** 2026-10-27
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0005](0005-telegram-ingress.md), [ADR-0006](0006-docker-image-and-compose.md) (both build on this decision)

## Context

Week 1 deliverable (product spec v2.1, §7 (private document)): a FastAPI Telegram webhook, Postgres and a job queue, deployed on a VM. The spec already sets:

- the queue: a Postgres table with `SELECT … FOR UPDATE SKIP LOCKED`, without Redis (§3.1, point 5);
- the `api`, `worker` and `postgres+pgvector` services in docker compose, on a VM and not on Cloud Run (§2.4, §3.2);
- a `Channel` abstraction, of which Telegram is only one adapter;
- an allowlist of Telegram ids (§3.7).

Still to be decided: the Postgres driver and migrations, the queue guarantees, the VM and HTTPS.

## Decision

1. **Two processes, one image.**
   - `api` (FastAPI) only acknowledges and enqueues.
   - `worker` runs the jobs.
   - They run in compose with `postgres` (`pgvector/pgvector:pg16`) and, in production, `caddy`.
2. **Webhook `POST /webhooks/telegram`.**
   - It checks the `X-Telegram-Bot-Api-Secret-Token` header with a constant-time comparison.
   - It validates a minimal `Update` and applies the allowlist (users and team group).
   - It inserts a job with the unique key `telegram:<update_id>` (`ON CONFLICT DO NOTHING`), then responds 200. It does nothing else.
   - Refused updates also get a 200, so that Telegram does not resend them, and they are logged.
3. **In-house queue, at-least-once delivery.**
   - **`jobs` table**: `kind`, `payload jsonb`, `status`, `attempts`, `run_after`, `locked_until`, unique `dedup_key`.
   - **Claiming a job**: `FOR UPDATE SKIP LOCKED`, which moves it to `running` and sets a lease (`locked_until`). The worker extends the lease during long jobs. A job whose lease has expired becomes eligible again, which covers the case of a dead worker.
   - **Failure**: retry with exponential backoff through `run_after`. After `max_attempts`, the job moves to the `dead` status to be inspected.
   - **Wake-up**: `LISTEN/NOTIFY` on the `jobs` channel, plus a fallback poll every few seconds, in case a notification is lost.
4. **psycopg 3 async, raw SQL.**
   - Queries in `db/repository.py`, rows mapped to Pydantic models in `db/models.py`.
   - Migrations: numbered SQL files in `db/migrations/`. A small runner applies them, each in a transaction, and records them in `schema_migrations`. It runs at deploy time, before `api` and `worker`.
   - This choice departs from the spec draft, which planned asyncpg. psycopg 3 is better typed for mypy strict, natively supports `LISTEN/NOTIFY` in async (`conn.notifies()`), and it is the reference driver. The performance gap does not matter at our volume.
5. **Telegram client written directly with `httpx`**, in `channels/telegram.py`, behind the `Channel` protocol of `channels/base.py`.
   - We only write the methods we need (`setWebhook`, `sendMessage`…), with a timeout, bounded retries and logging of every call.
   - We honor the `retry_after` that Telegram returns with its 429s.
   - We never apply a Markdown `parse_mode` to an LLM output (v1 bug #7). We send plain text, or HTML escaped by code.
6. **Hosting on Oracle Cloud, Ampere A1 (ARM64, Always Free).**
   - The base images (`python:3.12-slim`, `pgvector/pgvector:pg16`) exist for arm64.
   - If the Oracle account is refused, we fall back to a GCP e2-micro (1 GB of RAM, with swap).
7. **HTTPS: Caddy, which handles Let's Encrypt automatically.**
   - Target hostname: a subdomain of dzdia.com (for example `bot.dzdia.com`), to be requested during the discovery meeting.
   - Meanwhile, a free DuckDNS subdomain.
   - The `*.onrender.com` domain Abdou already uses is not suitable: it only applies to services hosted on Render.

## Alternatives considered

- **Redis + Celery or RQ**: one more service for a throughput of a few messages per hour.
- **procrastinate or pgqueuer**: these libraries rely on the same Postgres mechanisms. The in-house code is about 150 lines, fully under our control, and good interview material.
- **Keeping the transaction open for the whole job**, without a lease: this ties up a connection during LLM calls lasting several minutes.
- **Polling only**: kept, but only as a fallback for `LISTEN/NOTIFY`.
- **asyncpg**: its typing relies on third-party stubs.
- **SQLAlchemy 2 with Alembic**: too many concepts for a few tables, and pgvector and `tsvector` still require SQL.
- **aiogram or python-telegram-bot**: these frameworks manage the lifecycle and the handlers themselves, competing with our queue and our agent loop.
- **Long polling (`getUpdates`)**: it would do without HTTPS, but a process would have to run permanently to read messages, and the spec chose the webhook.
- **A worker as an asyncio task inside the API**: a blocking job or a crash would delay acknowledgements.
- **A self-signed certificate passed to `setWebhook`**: manual renewal, and an uncommon setup.

## Consequences

- **At-least-once delivery: a job can run twice.** Handlers must be idempotent, or record their effects. Example: the WordPress tool records the created draft before returning. A Telegram message sent twice is tolerated in week 1.
- **The `jobs` table grows over time**: finished jobs older than N days will have to be purged (later).
- **Week 1 scope**: a single job type, which replies in Telegram to prove the webhook → queue → worker → reply chain. The real flows arrive in week 2.
- **What Abdou needs**: WSL and Docker on the development machine, an Oracle Cloud account, a bot created with BotFather. The `.env` file also needs `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and the allowlist ids.
- **To add to the discovery questions (§9)**: get a dzdia.com subdomain and the id of the team's Telegram group.
