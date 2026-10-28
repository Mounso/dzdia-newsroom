# ADR-0005: Telegram ingress: normalized message, allowlist, webhook registration and JSON logs

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0004](0004-runtime-foundation.md) (builds on it), [ADR-0006](0006-docker-image-and-compose.md)

## Context

[ADR-0004](0004-runtime-foundation.md) sets the webhook → queue → worker → reply chain, but not four points needed to write `api/` and `channels/`:

- what the API puts in the job `payload`;
- where the allowlist lives, and which rule it applies in the team group;
- who calls `setWebhook`;
- the log format.

## Decision

1. **The job carries a normalized message, with the raw `Update` attached.**
   - The API converts the `Update` into an `InboundMessage` (in `channels/base.py`): `channel`, `chat_id`, `user_id`, `message_id`, `text`, `sent_at`, plus a `raw` field that keeps the full `Update`.
   - Ids are strings, so as not to assume a Telegram-specific format.
   - The worker only reads the normalized fields. `raw` is for debugging and replay.
   - In week 1, only text messages (`message.text`) are accepted. Other updates (edited messages, photos without a caption, channel posts without an author…) get a 200 and are logged as ignored.
2. **Allowlist in environment variables, and the sender must be on it everywhere.**
   - Variables: `TELEGRAM_ALLOWED_USER_IDS` (list of ids) and `TELEGRAM_TEAM_CHAT_ID`.
   - A message is accepted if its sender (`from.id`) is on the list **and** it arrives either in a private chat or in the team group. A listed journalist writing from another group is refused.
   - The rule fails closed: an empty list refuses everyone.
   - Refusals get a 200 ([ADR-0004](0004-runtime-foundation.md)) and are logged with the user and chat ids, **without the message text**.
3. **`setWebhook` goes through a CLI command run at deploy time**, modeled on `db.migrate`: `python -m dzdia_newsroom.channels.telegram set-webhook`.
   - It sends the public URL, the `secret_token` and `allowed_updates=["message"]`. The `--drop-pending` option discards pending updates.
   - The call is idempotent: it can be rerun at every deployment.
   - The API therefore only knows the webhook secret, never the bot token. Only the worker and the CLI need it (least privilege).
4. **JSON logs, one line per event, with the standard library only.**
   - A `logging_setup.py` module provides a `JsonFormatter`. `api` and `worker` install it at startup, and the uvicorn loggers go through it.
   - Fixed fields: `ts`, `level`, `logger`, `msg`. The fields passed in `extra` (`job_id`, `update_id`, `latency_ms`…) are added, and become filterable in `docker logs`.
   - Never a secret or a message text in the logs.
   - `LOG_FORMAT=text` keeps a readable output in development.

## Alternatives considered

- **Raw payload only**: the worker would depend on the Telegram format, which breaks the `Channel` abstraction.
- **Normalized payload only**: we would lose the fields we did not plan for, which are useful for debugging or replay.
- **Allowlist in a Postgres table**: it would need a migration and a way to administer the table, for about 5 journalists and a list that rarely changes.
- **Accepting any member of the team group**: someone added to the group by mistake would get access to the tools.
- **`setWebhook` at API startup**: the call would be repeated at every restart, and the API would need to know the bot token and its public URL.
- **structlog**: one more dependency, when a formatter of about thirty lines is enough.

## Consequences

- New dependencies: `fastapi` and `uvicorn`.
- Adding a journalist means editing `.env` and then restarting `api`.
- The Telegram settings are split: the API receives the secret and the allowlist; the worker and the CLI receive the bot token.
- Non-text messages (photos, documents, voice notes) are ignored until a flow needs them (week 2 or later).
- In the future, a second channel (WhatsApp, web interface) will only have to produce an `InboundMessage`.
