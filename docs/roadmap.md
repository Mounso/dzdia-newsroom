# Roadmap

Where the project stands and what comes next. The plan follows the product spec v2.1 (private document); every new module starts with an ADR.

## Principles

- **Measure before building.** Each evaluation set is built before the feature it measures.
- **Workflows where the steps are known, an agent only where the path depends on what it finds.** The morning briefing is a scheduled workflow; `/veille <topic>` is the only true agent loop.
- **Human approval before any write.** Nothing reaches WordPress without a persisted approval.

## Plan

| Week | Deliverables | Status |
|---|---|---|
| 1 | Repository foundation (uv, ruff, mypy strict, pytest, CI). v1 hallucination baseline. Telegram webhook, Postgres job queue and worker. Docker stack. Deployment on the VM. | Code done and tested end to end with a real bot. Baseline done on 2026-11-13, labels read again on 2026-11-14 and reported per task since then ([ADR-0016](adr/0016-baseline-reported-per-task.md)): given only the URL, v1 writes articles in which 57 % [48–67 %] of the checkable elements are unsupported by the source (72/126); given the page text, the same prompt leaves 12.9 % [8.5–18.1 %] unsupported (33/256) ([results](evals/baseline-v1.md#results)). VM deployment decided (ADR-0007) and scripted in `deploy/`; first run on the VM pending (stage 3). |
| 2 | Article flow for web links and pasted text: content extraction with SSRF guard, LLM providers (real and fake), structured outputs with retry, evidence and citations by id, synthesis card, angle choice, article, approval workflow, draft on a local WordPress. Faithfulness eval. | Not started |
| 3 | Audio and video (transcription, `yt-dlp`). RAG over the dzdia.com archive: sync, chunking, hybrid search (pgvector + Postgres full text), RRF, reranking, recall report. Internal links, "already covered" alert, proper-noun glossary. | Not started |
| 4 | Morning briefing (scheduled workflow with assignment buttons). `/veille` research agent with a hand-written loop and budgets. Guardrails and a prompt-injection test set. | Not started |
| 5 | MCP server over the same tools, tracing (Langfuse), real evals in CI with quality gates, calibrated LLM judge, GDPR and AI Act note, metrics in the README. | Not started |
| Later | Feedback from journalists, then an agent framework version for comparison. | Not started |

## Stages

Since 2026-11-09 the work is ordered in three stages; each one finishes before the next starts.

| Stage | Goal | Status |
|---|---|---|
| 1 | Final v1 hallucination baseline: extractor choice ([ADR-0008](adr/0008-baseline-extractor-model.md)), B7 and R8, `extract`, `match`, human labeling, `report`. | Done on 2026-11-13; reported per task since 2026-11-14 ([ADR-0016](adr/0016-baseline-reported-per-task.md)) |
| 2 | A vertical slice of v2: one link in, one sourced article out in Telegram, then measured with the baseline protocol against `page_text`. Out of scope: angle choice, WordPress, approval workflow, audio and video, pasted text, RAG. | Started on 2026-11-13: architecture decided ([ADR-0009](adr/0009-v2-link-to-sourced-article-slice.md), amended by [ADR-0011](adr/0011-failed-quote-is-removed.md) after the [independent review](reviews/2026-11-13-independent-review.md), and by [ADR-0012](adr/0012-validator-closes-three-gaps.md) for three validator rules) and comparison protocol fixed ([ADR-0010](adr/0010-v2-comparison-protocol.md)). Evidence, the `llm/` package and article generation with validation are written; the two eval prompts were tried on six pages outside the sample and frozen on 2026-11-13 ([v2 prompt tryout](evals/v2-tryout.md)). The comparison harness is decided ([ADR-0013](adr/0013-v2-comparison-harness.md)) and its `run` step is written ([v2 comparison](evals/v2-comparison.md)). The comparison was run, labeled blind and reported on 2026-11-14 ([results](evals/v2-comparison.md#labels-and-report-2026-11-14)): against the v1 prompt given the page text, v2 lowers the unsupported rate by 10.3 points [5.8; 15.3], but a plain prompt with the same instructions does as well (0 unsupported elements of 227, against 5 of 194 for v2), so the gain comes from the prompt, not from the architecture. A guard on quotes follows from that result ([ADR-0014](adr/0014-quote-must-be-quoted-in-source.md)): a quote is kept only when the source puts its words inside quotation marks. It is written, and replayed on the recorded drafts it rejects 5 of the 16 kept quotes, the 2 misattributed ones included ([replay](evals/v2-comparison.md#guard-replay-2026-11-14)); its prompt, `v2-article-2`, was tried on the six pages outside the sample and frozen on 2026-11-14 ([tryout](evals/v2-tryout.md#v2-article-2-2026-11-14)). A local copy of the outlet's own articles was collected on 2026-11-14 as a style reference ([ADR-0015](adr/0015-dzdia-corpus-local-copy.md), [counters](evals/dzdia-corpus.md)). The v1 figures are reported per task since 2026-11-14, with the article cell labeled in full and every other figure printed as a floor ([ADR-0016](adr/0016-baseline-reported-per-task.md)). Next: the place of the v2 architecture. Target: 2026-11-22 at the latest. |
| 3 | Production VM and real use by the team. | Not started |

## Next steps

1. Stage 2, in order, with a review by Abdou after each step: evidence, the `llm/` package, article generation and validation (done); the prompt tryout and freeze (done, [results](evals/v2-tryout.md#results)); the v2 eval on the stored snapshots, following [ADR-0010](adr/0010-v2-comparison-protocol.md) (`plain_prompt` and `v2` conditions in the harness, then run, extract, match, blind labeling of every element, comparison report); fetching behind the SSRF guard; worker and Telegram wiring, tested end to end with the dev bot. The eval comes before the wiring because it replays stored pages and needs neither the network nor Telegram (review finding V-12).
2. LLM-judge pass on the labeled elements (v1, then v2), calibrated against the human labels (Cohen's kappa); its verdicts are stored apart and never replace a label.
3. Later week 2 ADRs, not needed by the slice: workflow state (A4), worker concurrency (A2, R6), log correlation (A5).
4. Stage 3: discovery session with the journalists (briefing sources, a `dzdia.com` subdomain for the webhook, the team group id, journalists' Telegram ids), then the deployment on the Oracle Cloud Ampere VM: console setup, `deploy/bootstrap.sh`, first `deploy/deploy.sh`, production webhook, first backup and restore drill ([deployment](operations/deployment.md#first-installation)).
