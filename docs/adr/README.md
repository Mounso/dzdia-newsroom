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
| 0001 | [Measurement protocol for the v1 hallucination baseline](0001-baseline-hallucination-protocol.md) | Accepted | 2026-10-27 | Amended by 0002, 0003, 0010 |
| 0002 | [Algérie 360 replaces El Watan in the baseline sample](0002-algerie-360-replaces-el-watan.md) | Accepted | 2026-10-27 | Amends 0001 |
| 0003 | [Automated reference review, headlines included, paywalled articles excluded](0003-automated-reference-review.md) | Accepted | 2026-10-27 | Amends 0001 |
| 0004 | [Runtime foundation: Telegram webhook, Postgres queue, worker and deployment](0004-runtime-foundation.md) | Accepted (except the HTTPS hostname) | 2026-10-27 | Extended by 0005, 0006, 0007 |
| 0005 | [Telegram ingress: normalized message, allowlist, webhook registration and JSON logs](0005-telegram-ingress.md) | Accepted | 2026-10-27 | Builds on 0004 |
| 0006 | [Docker image, compose layout, migrations and secret distribution](0006-docker-image-and-compose.md) | Accepted | 2026-10-27 | Builds on 0004, 0005; extended by 0007 |
| 0007 | [Production host on Oracle Cloud: access, firewall, deployment, backups and hostname](0007-production-host-on-oracle-cloud.md) | Accepted | 2026-10-28 | Builds on 0004, 0006 |
| 0008 | [Gemini 3.1 Pro (preview, low thinking) extracts the elements of the v1 baseline](0008-baseline-extractor-model.md) | Accepted | 2026-11-09 | Related to 0001 |
| 0009 | [v2 vertical slice: a web link becomes a sourced article in Telegram](0009-v2-link-to-sourced-article-slice.md) | Accepted | 2026-11-13 | Builds on 0004, 0005; amended by 0011, 0012 |
| 0010 | [Comparison protocol for the v2 eval: full labels, a plain-prompt arm, and a conclusion fixed in advance](0010-v2-comparison-protocol.md) | Accepted | 2026-11-13 | Amends 0001; related to 0008, 0009, 0011, 0012, 0013 |
| 0011 | [A quote that fails validation is removed, not reworded](0011-failed-quote-is-removed.md) | Accepted | 2026-11-13 | Amends 0009 |
| 0012 | [The article validator removes a quote with an unknown id, and refuses quotation marks outside quotes and empty drafts](0012-validator-closes-three-gaps.md) | Accepted | 2026-11-13 | Amends 0009; related to 0010, 0011 |
| 0013 | [The v2 comparison runs in its own command, with a blind sheet, its own labels file and intervals fixed in advance](0013-v2-comparison-harness.md) | Accepted | 2026-11-13 | Related to 0001, 0003, 0008, 0010 |

> ADRs 0001-0006 were written in French and translated to English on 2026-10-28 with no change in substance; the French originals are in the git history (`docs/architecture_decisions.md`, commit 8e32200).
