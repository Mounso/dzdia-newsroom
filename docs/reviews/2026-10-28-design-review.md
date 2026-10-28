# Design review — 2026-10-28

An end-of-week-1 review of the whole codebase, before starting the AI features. It looks for bugs, robustness and security gaps, and checks whether the architectural choices still fit what is coming. Each finding has an id, referenced from the rest of the documentation, and a status that is updated as fixes land.

- **Scope:** everything tracked in Git at commit `8e32200`: `src/`, `evals/`, `tests/`, Dockerfile, compose, Caddy, CI and ADRs 0001 to 0006.
- **Method:** full code reading; `pytest`, `mypy --strict`, `ruff check`, `ruff format --check`; targeted runs to confirm suspected bugs.
- **Not covered:** live behavior of Telegram and Gemini.

## Summary

- **Overall state: strong for a first week.** 127 tests pass, including the Postgres integration tests; strict typing and lint are clean; every structural choice has an ADR and tests.
- **No critical bug.** One medium bug affects production (a long Telegram rate limit can bury a job), and one latent defect affects the evaluation (human labels can attach to the wrong element after a re-extraction).
- **Architecture: fit for purpose.** Five questions must be settled before or during week 2 (section 4).

Severity: **Medium** means data or measurement can be lost in realistic conditions; **Low** means a nuisance, a misleading log or an edge case.

Status: **Fixed** (with the change that fixed it), **Open**, or **Planned** (scheduled with a later feature).

## 1. Bugs

| Id | Severity | Finding | Status |
|---|---|---|---|
| B1 | Medium | A long Telegram 429 loses the message (see below). | Fixed |
| B2 | Low | `python -m dzdia_newsroom.db.migrate` crashed on Windows (`ProactorEventLoop`) and did not use the JSON log setup. | Fixed |
| B3 | Low | `CREATE TABLE IF NOT EXISTS schema_migrations` ran before the advisory lock; two concurrent runners could collide on `pg_type`. | Fixed |
| B4 | Low | The worker ignored the result of `retry_later` and `mark_dead`, and logged "retry in N s" or "dead" even when its lease was lost and the row unchanged. | Fixed |
| B5 | Medium (latent) | Eval element ids depend on position in the extractor output. | Fixed |
| B6 | Low | The output-level rate in the eval report is biased low while elements are pending. | Fixed |
| B7 | Low | The number regex of the matcher merges two adjacent numbers. | Open |

### B1 — A long Telegram 429 loses the message

- **Where:** [`channels/telegram.py`](../../src/dzdia_newsroom/channels/telegram.py) (`_call`, 429 branch) and [`worker/queue.py`](../../src/dzdia_newsroom/worker/queue.py) (`_fail`).
- **What happens:** when `retry_after` exceeds 30 s, the client raises a `ChannelError` that does not carry the delay. The queue then applies its own backoff: 0 to 5 s after the first failure, 0 to 10 s after the second. All three attempts fall inside the rate-limit window and the job goes `dead`; the journalist never gets an answer.
- **Same family:** the Gemini client of the eval harness ignores the `retryDelay` of 429 responses.
- **Fix (option A of three, chosen by Abdou):** `ChannelError` carries `retry_after_s`. The handler translates it into a new queue exception, `RetryLaterJobError(delay_s)`, the same way it already turns `PermanentChannelError` into `PermanentJobError`, so the queue still knows nothing about channels. The worker waits `max(backoff, delay_s * (1 + 0.1 * rand))`. The attempt still counts, and a retry-after over one hour (`max_retry_after_s`) kills the job. Covered by tests in `test_queue.py`, `test_handlers.py` and `test_telegram_client.py`.
- **Options set aside:** (B) a shared error taxonomy in a neutral `errors.py` module, read by the worker without translation; worth revisiting with the LLM client (R8) if translations multiply. (C) A "snooze" that does not consume an attempt, which needs a deadline to avoid endless loops.
- **Still open:** the Gemini client's `retryDelay` (R8).

### B2, B3, B4 — fixed

- **B2:** the migration entry point now uses `event_loop_factory()` and `configure_logging`, like `api` and `worker`, with an explicit logger name. Verified by running it on Windows: it now fails only on the unreachable test database, as expected.
- **B3:** the advisory lock is taken before creating `schema_migrations`.
- **B4:** `_mark_dead` and `_fail` check the fenced update's result; a lost lease is logged as a warning and nothing claims a state change that did not happen. Covered by `test_failure_after_lease_loss_is_not_logged_as_handled`.

### B5 — Eval labels can attach to the wrong element

- **Where:** [`evals/baseline_v1/extract.py`](../../evals/baseline_v1/extract.py), `element_id = f"{run_id}#{index}"`.
- **What happens:** if a run is extracted again (planned, since the extractor model will change), `run#3` may designate another element, and a label already entered silently attaches to it. No labels exist yet, so nothing is wrong today.
- **Fix (option A of three, chosen by Abdou):** a content-derived id, `<run_id>#<type>:<sha256 of type and normalized value, 10 hex>`, with `~2`, `~3` for repeats of a value within a run (so the metric's unit is unchanged). Normalization is Unicode NFC, case folding and whitespace collapsing; accents are kept, so a changed spelling is labeled again rather than merged. A changed element gets a new id and goes back to pending: the failure mode is extra labeling, never a wrong label.
- **Options set aside:** (B) keep positional ids and store a fingerprint of the element in `HumanLabel`, checked at import: it detects instead of preventing, and nearly every label goes stale after a re-extraction. (C) Ids tied to one extraction: the strictest lineage, but any re-extraction voids every label of the run. The value itself cannot be copied into `HumanLabel`, since `labels.jsonl` is committed and must not hold model output.
- **Related, fixed too:** `ExtractionRecord` now has a required `created_at` and a `latency_ms`. Tokens wait for R8 (the structured client drops `usage_metadata`). The 20 extractions made before the fix had no timestamp and were moved aside locally (`evals/.data/baseline_v1/pre_b5/`); all 144 will be extracted again with one model.

### B6 — Output-level rate is a lower bound while labels are pending

- **Where:** [`evals/baseline_v1/report.py`](../../evals/baseline_v1/report.py), `output_rate`.
- **What happens:** an output whose elements are still pending counts as an output with no unsupported element. The bias disappears once every `not_found` element is labeled.
- **Same cause, wider than the finding:** the element-level rate dropped pending elements from its denominator. Pending elements are all `not_found`, the ones most likely to be unsupported, so they are not missing at random and dropping them biased that rate low as well.
- **Fix (option B of three, chosen by Abdou):** worst-case (Manski) bounds for the element and output rates: pending counted as supported (low bound), then as unsupported (high bound). The bounds need no assumption about the pending elements and meet once labeling is complete, so the final report shows a single estimate as before. While they differ, the cell reads `low–high [CI]`, where the CI runs from the lower end of the low bound's interval to the upper end of the high bound's; both bootstraps use the same resamples. An output is *undetermined* only if it has pending elements and no unsupported one yet: one unsupported element decides it whatever the rest turn out to be. The report gains an "Undetermined outputs" column, a pending count in the per-type table, and a "Provisional report" banner while anything is pending. The contradicted rate and the per-type rates stay over decided elements, with their pending counts shown, since a "pending = contradicted" bound means nothing. Covered by `test_report.py`.
- **Options set aside:** (A) exclude undetermined outputs from the denominator: still biased, because the excluded outputs are the ones most likely to be positive, and the denominator shrinks silently. (C) keep one number and present it as a lower bound: half the information of B for nearly the same code.

### B7 — Adjacent numbers merged

- **Where:** [`evals/baseline_v1/matcher.py`](../../evals/baseline_v1/matcher.py), `_NUMBER`.
- **What happens:** "En 2023 450 artistes" yields the single number `2023450` (verified). The token fallback often recovers the element, but "450 participants" would be `not_found`.
- **Impact:** extra human labeling only; every `not_found` element is labeled, so the final rates are not biased.

## 2. Robustness and optimizations

| Id | Topic | Finding | Proposal | Priority | Status |
|---|---|---|---|---|---|
| R1 | Worker shutdown | A job cancelled after the 25 s grace period stayed `running` until its lease expired (60 s) and consumed an attempt. Two deployments during a long job could bury it. | Fenced `JobRepository.release()`: back to `queued`, `attempts - 1`, token cleared, `run_after` unchanged; called on cancellation, shielded and bounded to 3 s. Option A of three (the others: only cut the lease short, or lengthen the grace period). | Medium | Fixed |
| R2 | Connection pool | Without `check=`, the first request after a Postgres restart used a dead pooled connection. | `check=AsyncConnectionPool.check_connection`. | Low | Fixed |
| R3 | `LISTEN` connection | No TCP keepalive: a half-open connection blocks `notifies()` silently; polling (5 s) compensates. | `keepalives*` parameters in the connection string. | Low | Open |
| R4 | `jobs` growth | No purge; the table keeps message text and the raw update forever. | Scheduled purge of `done` jobs after N days, keeping at least 24 h for Telegram deduplication. ADR needed. | Medium | Planned |
| R5 | Silent dead jobs | When a job dies, the user receives nothing and nobody is alerted. | Fallback message to the user, plus an alert (monitored ERROR log or message to an admin chat). | Medium | Planned (week 2) |
| R6 | Worker concurrency | One job at a time. LLM calls last up to 88 s, so one long article would block every other message. | N concurrent slots bounded by a semaphore; `SKIP LOCKED` already supports it. | Medium | Planned (week 2) |
| R7 | Replay after downtime | Telegram keeps updates for 24 h; the bot will answer messages hours old. | Ignore or flag messages whose `sent_at` exceeds a threshold. | Low | Open |
| R8 | LLM client | `retryDelay` ignored, no rate limiter, latency and cost not logged; structured output discards `usage_metadata`. | Handle with A3. | Medium | Planned (week 2) |
| R9 | Health checks | `/health` does not check the database; the worker has no health check, so a stuck worker goes unnoticed. | `/health/ready` with `SELECT 1`; a worker heartbeat file checked by compose. | Low | Open |
| R10 | Telegram limits | `sendMessage` rejects more than 4096 characters (400, treated as permanent). | Split long texts (articles). | Low | Planned (week 2) |
| R11 | Worker log fields | The worker puts `job_id`, `kind` and attempt in the message text, not in `extra` fields, so they cannot be filtered as fields. | Pass them in `extra`; see A5 for request correlation. | Low | Open |

## 3. Security and supply chain

| Id | Finding | Proposal | Status |
|---|---|---|---|
| S1 | Images pinned by tag only (`cloudflared:latest`, `caddy:2`, `pgvector:pg16`, `python:3.12-slim-bookworm`); GitHub Actions pinned by tag, not SHA; no automated updates. | Pin exact versions (digests in production); add Dependabot or Renovate. | Fixed |
| S2 | CI does not build the Docker image (a broken Dockerfile shows up only at deployment) and does not audit dependencies. | `docker build` step; `pip-audit` or `osv-scanner` on `uv.lock`. | Fixed |
| S3 | The stored `raw` update contains personal data, kept indefinitely. | Minimize `raw` or purge it earlier; document retention (with R4). | Planned |
| S4 | A `POSTGRES_PASSWORD` containing `@`, `/` or `:` breaks the `DATABASE_URL` built by compose. | Documented in the [configuration reference](../operations/configuration.md#generating-secrets). | Fixed (documented) |
| S5 | The webhook accepts any source IP; the secret is the control. | Optional defense in depth: allow only Telegram's IP ranges in Caddy. | Open (kept open by [ADR-0007](../adr/0007-production-host-on-oracle-cloud.md): a change of ranges would cut the bot off silently) |
| S6 | Development tooling: the AI assistant's deny rule covers `Read(./.env)` but not shell commands such as `cat .env`. | Add matching `Bash(...)` deny rules (partial defense). | Open |

### S1 and S2 — fixed

- **S1 (option B of three, chosen by Abdou):** every image is pinned by exact tag and multi-arch index digest (the VM is arm64), including the `# syntax=` frontend and the images the CI pulls implicitly (`tonistiigi/binfmt` for QEMU, `moby/buildkit` for buildx). Actions are pinned by full commit SHA with the version in a comment. Dependabot updates `uv`, `github-actions`, `docker` and `docker-compose` weekly, minor and patch grouped, majors separate; Python stays on 3.12 until a deliberate upgrade. [`tests/test_supply_chain.py`](../../tests/test_supply_chain.py) fails on any unpinned reference and on drift Dependabot cannot fix (CI Postgres image, uv version in `ci.yml`, the two Python stages of the Dockerfile), and pins the Postgres major and Debian release (`pg16-bookworm`): a major upgrade needs a dump and restore, a new glibc can change collations. `pg16` pointed to `0.8.7-pg16-bookworm` already, so the existing volume sees no change.
- **Found while fixing S1:** the two Dockerfile stages could run different Python patch releases (the venv was built by the uv image's Python and run by `python:3.12-slim`'s). The build stage now uses the same pinned Python image as the runtime and copies the uv binary from a pinned uv image. uv also no longer publishes versioned `bookworm` tags, which the old floating tag hid.
- **S2 (option B of three):** an `image` CI job builds for `linux/amd64` and `linux/arm64` (QEMU) with the GitHub Actions cache, then imports every entry point in the amd64 image as the non-root user. An `audit` job runs `pip-audit --strict` on every group of `uv.lock`, exported with hashes, on every push and weekly; accepted advisories need an entry with a reason and review date in `.github/pip-audit-ignore.txt`.
- **CI hardening (same change):** read-only token (`permissions: contents: read`), `persist-credentials: false`, job timeouts, concurrency that cancels superseded runs except on `main`, uv and Python pinned in `setup-uv`. Checked with `actionlint` (and its `shellcheck` pass).
- **Options set aside:** (S1-A) exact tags without digests: readable, but a tag can be republished. (S1-C) Renovate: richer (automerge, dashboard), but a third-party app with access to a private repository, for little gain with one developer. (S2-A) amd64 build only: proves nothing about the production architecture. (S2-C) image scanning with Trivy: noisy on Debian advisories without fixes; worth adding once the VM runs, as digest updates already bring the base image fixes.
- **To verify on the first run:** that Dependabot groups digest-only updates as expected, and the CI duration of the arm64 build (72 s locally under Docker Desktop's emulation).

Strengths worth keeping: constant-time secret comparison; fail-closed allowlist; per-process secrets (the API never sees the bot token); token redaction and silenced `httpx` request logs; allowlist `.dockerignore`; non-root runtime image; two public routes with a body limit; no `parse_mode` on outgoing messages.

## 4. Architecture assessment

### What is state of the art for this context

- **Separate `api` and `worker`, with a Postgres queue**: `SKIP LOCKED`, lease renewed by heartbeat, fencing token, full-jitter backoff, permanent errors. This is the reference design when throughput is low and an extra broker is not worth operating. Building it rather than using a library is a documented choice (ADR-0004).
- **`LISTEN/NOTIFY` with polling as a safety net**, and the wake event cleared before draining, so no notification is lost.
- **psycopg 3 with raw SQL** and numbered migrations under an advisory lock: simple, typed, readable.
- **`Channel` abstraction** with a normalized `InboundMessage` and the raw update kept for debugging.
- **Per-process secret distribution**, compose profiles, Caddy.
- **Engineering discipline**: ADRs with rejected alternatives, strict typing, integration tests against a real Postgres, an evaluation baseline designed before the system it measures.

### To decide before or during week 2 (one ADR each)

- **A1 — External side effects are not protected by fencing.** The claim token stops a stale worker from writing to the database, not from calling Telegram or WordPress a second time; this is the classic limit of lease-based locks. For the WordPress tool (approval, then draft creation), an **effects ledger** is needed: an idempotency table keyed by `(job_id, step)`, written before the call and checked on replay, or an equivalent idempotency key on the WordPress side.
- **A2 — Worker concurrency** (R6): how many slots, and whether short replies need priority over long generations.
- **A3 — Where the LLM client lives.** `GeminiClient` sits in `evals/`, but v2 needs it in production. Proposal: a provider-agnostic `src/dzdia_newsroom/llm/` package (Gemini, then Groq) with rate limiting, `retryDelay` support, cost and latency logging, and structured output with retry. The evals import it, so **the client that is measured is the client that runs in production**.
- **A4 — Workflow state.** The invariant "any write tool requires a persisted `APPROVED` state" has no support yet. The `jobs` table is a queue, not a business state machine: a `workflows` (or `drafts`) table with explicit, SQL-guarded transitions is needed.
- **A5 — Log correlation.** A `trace_id` created at the webhook, carried in the job payload and put in the worker's log context (`contextvars`), to follow one request end to end with `docker logs | jq`.

### Consistency points

- `config.Settings` only serves the evals (it requires the Gemini key). Rename it `LLMSettings` when A3 is done.
- Settings getters are inconsistent: `get_settings` and `get_database_settings` are cached, while `api` and `worker` instantiate settings directly. Keep one convention.

## 5. Tests

- **Strong functional coverage:** every queue guarantee has a test (fencing, expired lease, concurrent claims, graceful stop, notification), as do the webhook (status codes, deduplication) and the Telegram client (short and long 429, 5xx, network errors, token redaction).
- **Coverage (added on 2026-10-28):** branch coverage of `src/` and `evals/` measured with Postgres: 69 %. The untested parts are mostly the entry points (`__main__` modules, `db/pool.py`, the JSON log setup), exercised only by the Docker smoke tests. `fail_under` is set to 68, a ratchet that only goes up.

## 6. Remediation plan

1. ~~**Quick fixes** (no design choice): B2, B3, B4, R2.~~ Done on 2026-10-28 (commit `dbd3e51`).
2. ~~**B1 and R1:** retries guided by `retry_after`, and releasing jobs on shutdown.~~ Done on 2026-10-28 (option A for both).
3. ~~**Evals:** B5 and the extraction timestamp, then B6, before running `extract` again.~~ Done on 2026-10-28 (option A for B5, option B for B6).
4. ~~**CI and supply chain:** S1, S2 and coverage measurement.~~ Done on 2026-10-28 (option B for both).
5. **Deployment** (not a finding, the next module): decided in [ADR-0007](../adr/0007-production-host-on-oracle-cloud.md) on 2026-10-28 and scripted in `deploy/`. S2-C (Trivy) is to be reconsidered once the VM runs.
6. **Week 2 (ADRs):** A1 to A5, R4, R5, R6, R8, R10.
