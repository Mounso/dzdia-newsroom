# Roadmap

Where the project stands and what comes next. The plan follows the product spec v2.1 (private document); every new module starts with an ADR. Day-to-day progress is tracked in [`STATUS.md`](STATUS.md).

## Principles

- **Measure before building.** Each evaluation set is built before the feature it measures.
- **Workflows where the steps are known, an agent only where the path depends on what it finds.** The morning briefing is a scheduled workflow; `/veille <topic>` is the only true agent loop.
- **Human approval before any write.** Nothing reaches WordPress without a persisted approval.

## Plan

| Week | Deliverables | Status |
|---|---|---|
| 1 | Repository foundation (uv, ruff, mypy strict, pytest, CI). v1 hallucination baseline. Telegram webhook, Postgres job queue and worker. Docker stack. Deployment on the VM. | Code done and tested end to end with a real bot; baseline paused at the extraction step; VM deployment next (ADR-0007). |
| 2 | Article flow for web links and pasted text: content extraction with SSRF guard, LLM providers (real and fake), structured outputs with retry, evidence and citations by id, synthesis card, angle choice, article, approval workflow, draft on a local WordPress. Faithfulness eval. | Not started |
| 3 | Audio and video (transcription, `yt-dlp`). RAG over the dzdia.com archive: sync, chunking, hybrid search (pgvector + Postgres full text), RRF, reranking, recall report. Internal links, "already covered" alert, proper-noun glossary. | Not started |
| 4 | Morning briefing (scheduled workflow with assignment buttons). `/veille` research agent with a hand-written loop and budgets. Guardrails and a prompt-injection test set. | Not started |
| 5 | MCP server over the same tools, tracing (Langfuse), real evals in CI with quality gates, calibrated LLM judge, GDPR and AI Act note, metrics in the README. | Not started |
| Later | Feedback from journalists, then an agent framework version for comparison. | Not started |

## Next steps

1. Discovery session with the journalists: briefing sources, a `dzdia.com` subdomain for the webhook, the team group id, journalists' Telegram ids.
2. Deploy on the Oracle Cloud Ampere VM (ADR-0007: backups, firewall, SSH, DNS).
3. Resume the baseline: choose the extractor model, fix B5 and B6, then `extract`, `match`, labeling and `report`.
4. Remaining fixes from the [design review](reviews/2026-10-28-design-review.md): B1 and R1, then CI and supply chain.
5. Week 2 ADRs: LLM package, effects ledger, workflow state, worker concurrency, log correlation.
