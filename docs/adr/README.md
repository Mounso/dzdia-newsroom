# Architecture decision records

An architecture decision record (ADR) captures one significant decision: the context that forced it, what was decided, the alternatives that were rejected and why, and the consequences we accept. Together they explain *why* the system looks the way it does, which the code alone cannot show.

## Rules

- **An accepted ADR is never edited.** If a decision changes, write a new ADR that amends or supersedes the old one, and update the status line and the index below. Only metadata (status, links) and obvious typos may be corrected in place.
- One decision per ADR, numbered sequentially (`0001`, `0002`, …). Numbers are never reused.
- Write the ADR before (or with) the code it justifies, and reference it from the code where useful (e.g. `# ADR-0004`).

## Lifecycle

| Status | Meaning |
|---|---|
| Proposed | Under discussion; not yet binding. |
| Accepted | In force. The code must follow it. |
| Superseded | Replaced by a later ADR, which is linked from the status line. |
| Deprecated | No longer relevant (the feature was removed), with no replacement. |

An ADR that only changes part of an earlier one *amends* it: both stay accepted, and each links to the other.

## Adding an ADR

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, using the next free number.
2. Fill in every section; set the status to *Proposed*, then to *Accepted* once decided.
3. Add a row to the index below, and update the status or relations of any ADR it amends or supersedes.

## Index

| # | Title | Status | Date | Relations |
|---|---|---|---|---|
| 0001 | [Measurement protocol for the v1 hallucination baseline](0001-baseline-hallucination-protocol.md) | Accepted | 2026-10-27 | Amended by 0002, 0003 |
| 0002 | [Algérie 360 replaces El Watan in the baseline sample](0002-algerie-360-replaces-el-watan.md) | Accepted | 2026-10-27 | Amends 0001 |
| 0003 | [Automated reference review, headlines included, paywalled articles excluded](0003-automated-reference-review.md) | Accepted | 2026-10-27 | Amends 0001 |
| 0004 | [Runtime foundation: Telegram webhook, Postgres queue, worker and deployment](0004-runtime-foundation.md) | Accepted (except the HTTPS hostname) | 2026-10-27 | Extended by 0005, 0006 |
| 0005 | [Telegram ingress: normalized message, allowlist, webhook registration and JSON logs](0005-telegram-ingress.md) | Accepted | 2026-10-27 | Builds on 0004 |
| 0006 | [Docker image, compose layout, migrations and secret distribution](0006-docker-image-and-compose.md) | Accepted | 2026-10-27 | Builds on 0004, 0005 |

> ADRs 0001-0006 were written in French and translated to English on 2026-10-28 with no change in substance; the French originals are in the git history (`docs/architecture_decisions.md`, commit 8e32200).
