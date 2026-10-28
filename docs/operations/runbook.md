# Runbook

This page lists operational procedures for a running stack. Each one follows the same pattern: symptoms, then diagnosis commands, then fix. Commands are run from the repository root on the host. Background on the queue semantics is in [ADR-0004](../adr/0004-runtime-foundation.md); variables are described in the [configuration reference](configuration.md).

## Reading logs

`api`, `worker` and `migrate` write one JSON object per line on stderr, with fixed fields `ts`, `level`, `logger` and `msg`, plus context fields passed through `extra`. Message text and secrets are never logged.

```bash
docker compose logs -f worker
docker compose logs -f api
```

Drop the `service-1 |` prefix to pipe the lines into `jq`:

```bash
# Warnings and errors of the worker
docker compose logs --no-log-prefix worker | jq -c 'select(.level == "WARNING" or .level == "ERROR")'

# API events for one Telegram update or one job (fields set by the webhook)
docker compose logs --no-log-prefix api | jq -c 'select(.update_id == "123456789")'
docker compose logs --no-log-prefix api | jq -c 'select(.job_id == 42)'

# Worker outcome of one job (the worker puts the job id in the message: "job 42 (kind, attempt 1/3): ...")
docker compose logs --no-log-prefix worker | jq -c 'select(.msg | startswith("job 42 "))'

# Bot API calls with their latency
docker compose logs --no-log-prefix worker | jq -c 'select(.method != null) | {ts, method, status, attempt, latency_ms}'
```

Set `LOG_FORMAT=text` in `.env` for human-readable lines during local development.

## Inspecting the queue

Open `psql` inside the Postgres container:

```bash
docker compose exec postgres psql -U dzdia -d dzdia
```

Columns of `jobs` (see [`0001_jobs.sql`](../../src/dzdia_newsroom/db/migrations/0001_jobs.sql)): `id`, `kind`, `payload`, `status` (`queued`, `running`, `done`, `dead`), `attempts`, `max_attempts`, `run_after`, `locked_until`, `claim_token`, `dedup_key`, `last_error`, `created_at`, `updated_at`.

```sql
-- Queue depth by status
SELECT status, count(*) FROM jobs GROUP BY status ORDER BY status;

-- Oldest job waiting to run
SELECT id, kind, attempts, run_after, now() - created_at AS age
FROM jobs WHERE status = 'queued' ORDER BY run_after, id LIMIT 5;

-- Running jobs whose lease expired (worker presumed dead; the next claim takes them over)
SELECT id, kind, attempts, max_attempts, locked_until
FROM jobs WHERE status = 'running' AND locked_until < now();

-- Dead jobs and why they died
SELECT id, kind, attempts, max_attempts, updated_at, last_error
FROM jobs WHERE status = 'dead' ORDER BY updated_at DESC LIMIT 20;
```

Avoid `SELECT payload` in shared sessions: `inbound_message` payloads contain the message text and the raw Telegram update.

### Requeue a dead job

Fix the cause first (see `last_error`), then:

```sql
UPDATE jobs
SET status = 'queued', attempts = 0, run_after = now(), last_error = NULL,
    claim_token = NULL, locked_until = NULL, updated_at = now()
WHERE id = 42 AND status = 'dead';
```

The worker picks it up within its 5-second poll interval. An `UPDATE` sends no `NOTIFY`; only `enqueue` does.

## The bot does not answer

**Symptoms:** a journalist sends a message and gets no "Bien reçu." reply.

**Diagnosis:**

1. Is Telegram reaching the webhook?

   ```bash
   docker compose run --rm telegram-cli webhook-info
   ```

   A non-empty `last_error_message` or a growing `pending_update_count` means Telegram cannot deliver: the URL is wrong or dead (in dev, the tunnel URL changed), the stack is down, or the secret does not match (see the next section).

2. Did the API refuse or ignore the update?

   ```bash
   docker compose logs --no-log-prefix api | jq -c 'select(.msg | startswith("update"))'
   ```

   - `update refused: sender or chat not allowed`: the sender is not in `TELEGRAM_ALLOWED_USER_IDS`, or the message comes from a group other than `TELEGRAM_TEAM_CHAT_ID`. The log shows `user_id` and `chat_id`.
   - `update ignored: ...`: not a text message (photo without caption, edited message, and so on).
   - `update queued`: the job exists, so the problem is on the worker side.

3. Is the worker running and processing jobs?

   ```bash
   docker compose ps worker
   docker compose logs --tail 50 worker
   ```

   Then check the queue for `queued` jobs that do not move, or for `dead` jobs.

**Fix:** depending on the cause, rerun `set-webhook` with the correct URL, [add the user to the allowlist](#add-a-journalist-to-the-allowlist), restart the worker (`docker compose up -d worker`), or [requeue the dead job](#requeue-a-dead-job).

## Telegram gets 401 from the webhook

**Symptoms:** `webhook-info` shows `last_error_message` with a 401 status; the API logs `webhook call with a wrong secret`.

**Cause:** the secret registered at Telegram differs from `TELEGRAM_WEBHOOK_SECRET` in the `api` container, usually after editing `.env` without running `set-webhook` again.

**Fix:** register the webhook again. `telegram-cli` reads the same `.env`:

```bash
docker compose run --rm telegram-cli set-webhook --url https://<host>/webhooks/telegram
```

## Rotate the webhook secret

1. Generate a new value (`openssl rand -hex 32`) and set `TELEGRAM_WEBHOOK_SECRET` in `.env`.
2. Recreate the API so it reads the new value, then register the webhook again:

   ```bash
   docker compose up -d api
   docker compose run --rm telegram-cli set-webhook --url https://<host>/webhooks/telegram
   ```

Updates delivered between the two commands get a 401. Telegram retries them, so none is lost.

## Rotate the bot token

1. In BotFather, revoke the current token and get a new one.
2. Set `TELEGRAM_BOT_TOKEN` in `.env`.
3. Recreate the worker, then register the webhook again so that it is attached to the new token:

   ```bash
   docker compose up -d worker
   docker compose run --rm telegram-cli set-webhook --url https://<host>/webhooks/telegram
   ```

## Add a journalist to the allowlist

1. Get the journalist's numeric Telegram user id.
2. Append it to `TELEGRAM_ALLOWED_USER_IDS` in `.env` (comma-separated, no spaces needed).
3. Recreate the API; `restart` would keep the old environment:

   ```bash
   docker compose up -d api
   ```

The allowlist lives in the environment by design ([ADR-0005](../adr/0005-telegram-ingress.md)): no migration or admin interface for about five users.

## Postgres restart

**What happens:**

- The API and worker pools test each connection when it is taken from the pool, and replace dead ones.
- The worker's `LISTEN` connection drops; it logs `LISTEN connection lost, reconnecting in N s` and reconnects with exponential backoff (1 s, doubling, capped at 60 s).
- While `LISTEN` is down, the worker still polls the queue every 5 seconds, so no job is missed.
- If Postgres stays down, webhook calls fail with 500 and Telegram redelivers them later. The dedup key `telegram:<update_id>` makes those redeliveries safe.

**Check after the restart:**

```bash
docker compose logs --no-log-prefix worker | jq -c 'select(.msg | startswith("listening"))'
```

## Worker graceful stop

On `SIGTERM` (`docker compose stop`, `up -d` after a rebuild) or `SIGINT`:

1. The worker stops claiming new jobs.
2. The current job gets up to 25 seconds to finish (`SHUTDOWN_GRACE_S`). Compose waits 30 seconds (`stop_grace_period`) before `SIGKILL`.
3. A job still running after 25 seconds is cancelled and released: it goes back to `queued` at once, and the attempt it was using is given back.
4. If the release fails (database unreachable, or more than 3 seconds), the job stays `running` until its 60-second lease expires, then the next claim takes it over. That claim counts as a new attempt.

The worker logs `stop requested, finishing the current job`, then `worker stopped`. A cancelled job adds `released at shutdown, back in the queue`, or `release failed at shutdown, its lease will expire` with a stack trace. On Windows outside Docker, signal handlers are not available: Ctrl+C cancels at once, without the grace period.

## Backups and restore

How backups work is described in [deployment](deployment.md#backups) and [ADR-0007](../adr/0007-production-host-on-oracle-cloud.md). The production `.env` is **not** in the backups: keep a copy in the password manager, next to the `age` private key.

### Check the last backups

```bash
systemctl list-timers dzdia-backup.timer          # last and next run
journalctl -u dzdia-backup.service --since -2d    # "restore check passed", "uploaded daily/..."
```

healthchecks.io alerts when a run fails or does not happen. In the Oracle console, the bucket should hold 7 to 8 objects under `daily/` and up to 4 under `weekly/`.

**A run failed:** read the journal. Common causes:

- `restore check: the restored database differs from the live one`: a migration ran during the dump. Run the backup again.
- an `rclone` error with `NotAuthorizedOrNotFound`: the dynamic group or its policy does not match (instance OCID, bucket name, compartment), or `OCI_*` is wrong in `.env`.
- `set BACKUP_... in .env`: a variable is missing.

### Take a backup by hand

```bash
sudo systemctl start dzdia-backup.service   # as ubuntu: same as the timer, with healthcheck pings
deploy/backup.sh predeploy                  # as deploy: goes to predeploy/, no healthcheck ping
```

### Monthly restore drill (workstation)

This proves the whole chain: upload, encryption and the private key. Run it on the workstation, where the private key is, never on the VM.

1. In the Oracle console, download the latest object from `daily/`.
2. Run, from the repository (WSL or Linux, with Docker and `age`):

   ```bash
   deploy/restore-drill.sh ~/Downloads/dzdia-<date>-daily.dump.age /path/to/dzdia-backup.key
   ```

3. The script restores into a throwaway Postgres and prints one line per table with its row count, plus the applied migrations. Check that they look right, and keep the encrypted file: it is the copy outside Oracle.

### Restore production from a backup

Plan for a few minutes of downtime. Telegram retries the webhook calls that fail meanwhile.

1. On the workstation, decrypt the chosen backup, then copy it to the VM:

   ```bash
   age -d -i /path/to/dzdia-backup.key -o dzdia.dump dzdia-<date>-daily.dump.age
   scp dzdia.dump deploy@<host>:
   ```

2. On the VM, as `deploy`, stop the writers, restore, and restart:

   ```bash
   cd dzdia-newsroom
   docker compose stop api worker
   docker compose exec -T postgres pg_restore -U dzdia -d dzdia --clean --if-exists --no-owner --exit-on-error < ~/dzdia.dump
   docker compose --profile prod up -d
   shred -u ~/dzdia.dump
   ```

3. Delete the plain dump on the workstation too.

### Rebuild the VM from nothing

Target: about one hour. Follow [first installation](deployment.md#first-installation) (new instance on the same reserved IP, `bootstrap.sh`, `.env` from the password manager, `deploy.sh origin/main`), then [restore production](#restore-production-from-a-backup). Update the dynamic group's matching rule with the new instance OCID.

## A deployment failed

**Symptoms:** `deploy.sh` stops with `docker compose up failed` (often a failed migration) or `https://<host>/health did not answer within 120s`.

1. Look at the state and the logs:

   ```bash
   deploy/deploy.sh --status
   docker compose logs --tail 100 migrate api caddy
   ```

2. If the cause is not obvious, return to the previous version at once, then investigate:

   ```bash
   deploy/deploy.sh --rollback
   ```

A failed `migrate` keeps `api` and `worker` on the previous containers, which compose did not recreate. Rollback does not revert the schema: migrations follow the expand/contract rule, so the previous code works with the new schema.

## Locked out of SSH

**Symptoms:** `Permission denied (publickey)` or a timeout on port 22.

- Check the key you use: only ed25519 keys are accepted, and only for `ubuntu` and `deploy`.
- A timeout means the security list or `iptables` drops port 22. Never use UFW on Oracle's images.
- **Recovery:** Oracle console → the instance → *Console connection* → *Launch Cloud Shell connection* (serial console). Log in as `ubuntu` with its console password (set at installation; without one, edit the boot entry to get a root shell). Then fix `/etc/ssh/sshd_config.d/10-dzdia.conf` or `/etc/iptables/rules.v4` (`/etc/iptables/rules.v4.orig` is Oracle's original).

## Host firewall

Inspect the live rules, and check that ports 80 and 443 come before the final `REJECT`:

```bash
sudo iptables -S INPUT
```

To open another port, insert the live rule after the SSH rule and add the same line to `/etc/iptables/rules.v4`, as `bootstrap.sh` does. Never run `iptables-restore` (it flushes Docker's chains, so containers lose their network until Docker restarts) or `netfilter-persistent save` (it would write Docker's chains into the boot file).

## Automatic updates and reboots

`unattended-upgrades` installs security updates and Docker updates every day, and reboots at 04:00 UTC when a package requires it. The containers come back by themselves.

```bash
cat /var/run/reboot-required 2>/dev/null       # a reboot is pending
less /var/log/unattended-upgrades/unattended-upgrades.log
sudo unattended-upgrade --dry-run --debug 2>&1 | grep -i "allowed origins"
```

The last command must list the Docker origin. If it does not, check the `Origin` and `Label` of Docker's repository in `/var/lib/apt/lists/*docker*Release`.
