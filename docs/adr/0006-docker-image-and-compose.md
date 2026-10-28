# ADR-0006: Docker image, compose layout, migrations and secret distribution

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0004](0004-runtime-foundation.md) (builds on it), [ADR-0005](0005-telegram-ingress.md)
- **Amended (2026-10-28, design review S1 and S2):** the build stage is now `python:3.12.x-slim-bookworm` with the uv binary copied from a pinned `ghcr.io/astral-sh/uv` image, so both stages share the exact same Python; every image is pinned by tag and digest. The CI builds the image for amd64 and arm64 without pushing it (no registry, so the rejected GHCR option below still stands). See [conventions](../development/conventions.md#dependencies-and-supply-chain).

## Context

[ADR-0004](0004-runtime-foundation.md) sets the services: `api` and `worker` (a single image), `postgres` (`pgvector/pgvector:pg16`) and `caddy` in production, all on an Oracle Ampere VM (arm64). The product spec plans a `dev` profile, with WordPress and MariaDB from week 2. [ADR-0005](0005-telegram-ingress.md) requires that the API never sees the bot token.

Still to be decided:

- how to build the image;
- how to separate development and production;
- when to apply migrations;
- how each container receives its secrets.

## Decision

1. **Two-stage Dockerfile, built on the machine that runs it.**
   - **Build stage** (`ghcr.io/astral-sh/uv:python3.12-bookworm-slim`): `uv sync --frozen --no-dev --no-editable`. Dependencies are installed before the code is copied, so that the layer stays cached as long as `uv.lock` does not change.
   - **Final stage** (`python:3.12-slim-bookworm`): only the `.venv`, and a **non-root user**. Neither uv nor build tools.
   - **Default command:** the API. The worker and `migrate` override `command`.
   - **Where it is built:** `docker compose build` on the VM (native arm64) as on the workstation (amd64). The base images exist for both architectures. No registry.
   - **No secret in the image:** a `.dockerignore` excludes `.env`, `.venv`, `evals/.data/` and local files.
2. **A single `docker-compose.yml`, with profiles.**
   - **No profile** (always started): `postgres`, `migrate`, `api`, `worker`.
   - **`dev` profile**: `cloudflared`, a quick tunnel without an account that gives a public HTTPS URL to `api`. WordPress and MariaDB will be added in week 2.
   - **`prod` profile**: `caddy`, the only service listening on ports 80 and 443. It only forwards `/webhooks/telegram` and `/health` to `api`, and responds 404 to everything else.
   - **Ports:** `api` and `postgres` are only published on `127.0.0.1`.
3. **Migrations through a one-shot `migrate` service.**
   - It starts when `postgres` is healthy (`pg_isready`).
   - `api` and `worker` depend on it with `condition: service_completed_successfully`: if a migration fails, nothing starts.
   - `set-webhook` remains a manual command, because it depends on the public URL: `docker compose run --rm telegram-cli set-webhook --url …`. The `telegram-cli` service (`tools` profile) is the only one to receive both the bot token and the webhook secret.
4. **A single `.env` for the human, an explicit variable list per service.**
   - Compose only reads `.env` for interpolation. Each service declares its variables in `environment:`. There is no shared `env_file:`.
   - Distribution:
     - `api` receives the webhook secret and the allowlist;
     - `worker` receives the bot token;
     - `migrate` only receives the database.
   - `DATABASE_URL` is built in compose from `POSTGRES_PASSWORD`.
   - Required variables use `${VAR:?message}`: compose refuses to start if one is missing.
5. **Default operational choices.**
   - Postgres data lives in a named volume.
   - Healthchecks: `pg_isready` for `postgres`, `/health` for `api`.
   - `restart: unless-stopped`.
   - `stop_grace_period: 30s` on `worker` ([ADR-0005](0005-telegram-ingress.md): 25 s of grace on the code side).
   - `json-file` logs are size-limited (rotation), so as not to fill the VM's disk.
   - Backups (`pg_dump`) are deferred to the deployment step.

## Alternatives considered

- **Single-stage image, on the uv image**: about twice as heavy, and it ships tools that are useless at runtime.
- **Multi-arch build in GitHub Actions, pushed to GHCR**: slow build under QEMU emulation, one more token to manage, and it is already continuous deployment. We will get there when we automate deployment.
- **Base file plus an override file** (`compose.prod.yml`): the `-f` flags have to be stacked, and it is easy to forget one.
- **ngrok** for development: one more account and token. The fixed URL is not worth that cost in week 1.
- **Migrations in an entrypoint script of each container**: repeated at every restart, and two roles in a single command.
- **A shared `env_file: .env`**: the API would receive the bot token, which contradicts [ADR-0005](0005-telegram-ingress.md).
- **Docker secrets (`/run/secrets`)**: secrets would no longer show up in `docker inspect`, but `secrets_dir` would have to be configured and one file per secret managed. Possible later.

## Consequences

- **The Postgres port is published on `127.0.0.1` in production too.** A single file does not allow publishing it in dev only. This is safe, because the loopback address is not reachable from outside and is not affected by Docker bypassing ufw. It also allows access to `psql` through an SSH tunnel.
- **The `cloudflared` URL changes every time the tunnel starts**, so `set-webhook` has to be rerun every time in dev.
- **Every deployment rebuilds the image on the VM**, in 1 to 2 minutes on Ampere.
- **Adding an environment variable takes two places:** in `.env` and in the list of the service concerned. That is the price of least privilege.
