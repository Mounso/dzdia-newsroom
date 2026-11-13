# ADR-0009: v2 vertical slice: a web link becomes a sourced article in Telegram

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0004](0004-runtime-foundation.md) (queue and worker), [ADR-0005](0005-telegram-ingress.md) (Telegram ingress), [ADR-0008](0008-baseline-extractor-model.md) (extractor); design review points [A1, A3](../reviews/2026-10-28-design-review.md#to-decide-before-or-during-week-2-one-adr-each), R5, R10. The comparison protocol is a separate ADR (ADR-0010).

## Context

Stage 2 of the [roadmap](../roadmap.md#stages) builds one vertical slice of v2, then measures it with the v1 baseline protocol ([ADR-0001](0001-baseline-hallucination-protocol.md)). The scope is fixed and narrow: a journalist sends a link in Telegram; v2 downloads the page, cuts its text into evidence items with stable ids, generates an article as structured output where every sentence cites evidence ids, validates those ids in code (one retry with the error), and sends the article and its sources back in Telegram. Angle choice, WordPress, the approval workflow, audio, video, pasted text and RAG are out of scope.

What the v1 baseline showed ([report](../evals/baseline-v1.md#results)), on the article task with the page text in the prompt (`page_text / article`):

- 7 % [5–11 %] of factual elements are unsupported (19/256), and 81 % of articles (13/16) contain at least one;
- quotes are the weak spot: 54 % [22–78 %] of quotes are unsupported in `page_text` (7/13, all tasks), mostly reworded words inside quotation marks.

Already in place: the Postgres queue with a heartbeat-renewed lease (a 90 s LLM call is safe), the Telegram client with `retry_after` support, and in `evals/baseline_v1/` a Gemini client with retries, request spacing and structured output, plus trafilatura extraction (`snapshot.extract_main_text`).

Two parts are Abdou's katas and are not written for him: the SSRF guard, and the structured-output validation-and-retry loop.

## Decision

1. **LLM client in `src/dzdia_newsroom/llm/`** (design review A3). `evals/baseline_v1/llm.py` moves there (`Protocol`s, `GeminiClient`, `retry_async`, `RequestSpacer`, `Structured`, `Usage`) with a `FakeLLM` for tests; the evals import it, so the client that is measured is the client that runs in production. `GeminiClient.generate()` keeps calling the model with no config (v1 defaults), guarded by a test, so the baseline stays reproducible. `config.Settings` becomes `LLMSettings` (consistency point of the review).
2. **Structured article, one citation list per sentence.** The schema is `{title, paragraphs: [{segments: [...]}]}`, where a segment is either a sentence `{kind: "sentence", text, evidence_ids}` or a quote (point 3). The title cites nothing; every sentence must cite at least one id.
3. **Quotes are typed segments, checked and rendered by code.** A quote is `{kind: "quote", speaker, text, evidence_id}`. The model never writes quotation marks: code checks that `text` is an exact substring of evidence `evidence_id` after normalization (Unicode NFC, collapsed whitespace, typographic apostrophes and quotation marks folded; no ellipsis support in this version), then renders `« text »`. A quote that still fails after the retry is rendered as indirect speech (`Selon <speaker>, <text>`, without quotation marks) and flagged in the sources message.
4. **Validation and one retry** (Abdou's kata). Errors: an unknown evidence id, a sentence with no id, a quote that is not a substring of its evidence. On errors, the model is called once more with the same prompt plus the list of errors. After the retry, id errors fail the job with a message to the journalist (point 9); quote errors fall back to indirect speech (point 3). The model never produces URLs: the source URL comes from the job payload.
5. **Evidence = paragraphs, ids `E1…En`.** The page text from trafilatura is split on paragraphs; a paragraph over about 600 characters is split at sentence ends. Ids follow document order and are scoped to one request; code keeps `id → (text, sha256)`. The text sent to the model is capped (30,000 characters); a truncation is stated in the sources message. Evidence goes into the prompt inside delimiters, as untrusted content: it can never trigger a tool.
6. **Fetching behind an SSRF guard** (Abdou's kata; `src/dzdia_newsroom/guardrails/ssrf.py`, as planned in [security](../architecture/security.md)). The host is resolved, every resolved address must be global (`ipaddress` `is_global`; loopback, private, link-local including `169.254.169.254`, and reserved addresses are refused), and the connection goes to the checked address, so a second DNS answer cannot change the target (DNS rebinding). Redirects are followed by hand, at most 5, each hop checked again. Limits: `http` and `https` only, ports 80 and 443, 2 MB body, `text/html` only, 15 s total. Extraction reuses `extract_main_text`, moved from `evals/` to `src/`.
7. **Tools in `src/dzdia_newsroom/tools/`** (architecture invariant): `fetch_page.py` (guarded download and extraction), `evidence.py` (split and ids), `article.py` (schema, versioned prompt `v2-article-1`, validation, generation). The worker handler and the Telegram rendering are thin adapters over them.
8. **One handler, intermediate results persisted** (design review A1, light version). The `inbound_message` handler runs fetch → evidence → generation → send in one job. Migration `0002_drafts.sql` adds `drafts(job_id unique, source_url, evidence, article, model, prompt_version, usage, created_at, sent_at)`. A replayed job reuses its draft instead of calling the LLM again, and sends only if `sent_at` is null. The full effects ledger waits for WordPress.
9. **Visible errors for the journalist** (design review R5, user side). Typed errors (`BlockedUrlError`, `FetchError`, `NoReadableTextError`, `GenerationFailedError`) each send a short French message, then become `PermanentJobError`. Any other error sends a generic message when the job goes `dead`, through a small hook in `worker/queue.py`. The admin alert waits.
10. **Telegram flow and rendering** (design review R10). A message with a link gets an immediate acknowledgment ("Lien reçu, article en préparation (~1 min)"); only the first link is processed, and the reply says so when there are several; a message without a link gets a short help text. The result is two plain-text messages (no `parse_mode`, which avoids MarkdownV2 escaping errors): the article with `[1]`, `[2]` markers, then "Sources" with the URL and a short excerpt (about 200 characters) of each cited evidence item. Texts over 4,096 characters are split at paragraph boundaries.
11. **Generator: `gemini-3.1-flash-lite-preview`**, the v1 model, at temperature 0 and default thinking, set by a new `GENERATION_MODEL` setting. Using the v1 model makes the comparison with `page_text` measure the architecture, not a model change. A stronger production model, if wanted later, gets its own eval condition.

**Kata split.** Claude writes the signatures, docstrings and tests that specify the SSRF guard and the validation-and-retry loop; Abdou writes their bodies; Claude reviews. Everything else (point 3's substring check included) is written by Claude.

## Alternatives considered

- **LLM client copied into `src/`, or imported from `evals/`**: two diverging clients, or production code depending on the eval package, which is not meant to ship.
- **Free text with `[E3]` markers parsed by code**: more natural writing, but malformed markers and uncited sentences bring back fragile parsing.
- **Two passes (fact list, then writing)**: facts checkable one by one, but two calls, twice the latency, and the writing pass can still add facts.
- **Quotes detected by regex in free text**: no schema change, but quotation-mark detection is brittle (nesting, English quotes, apostrophes).
- **No direct quotes at all**: simplest, but a journalistic loss, and a 0 % unfaithful-quote rate obtained for free.
- **Sentence-level evidence**: finer, but many ids and French sentence splitting is error-prone ("M.", "1,5"). **Fixed windows with overlap**: robust to any layout, but cuts sentences and is unreadable as a source.
- **SSRF check before the request only**: a time-of-check/time-of-use gap (DNS rebinding). **An egress proxy** (smokescreen): network-level defense, but extra infrastructure for one slice.
- **Accepting duplicates on replay**, as the "Bien reçu" handler does: a replay would pay for a second LLM call and send two articles. **A generic effects ledger now**: designed before it has two users.
- **A chain of jobs per step**: per-step retries, but more job kinds and payloads for little gain at this size.
- **One long message with footnotes**, or **sources as an attached file**: harder to read on a phone.
- **A stronger generator**: better prose, but the gain could no longer be attributed to the architecture.

## Consequences

- The v2 eval can compare v2 with `page_text / article` on equal terms: same model, same articles, same extractor and labels; only the pipeline differs. The protocol (ADR-0010) is written before any v2 code runs on the eval set.
- Quote fidelity becomes a property guaranteed by code. The eval then checks the guard, and must also count quotes kept and quotes downgraded to indirect speech, so that "no quotes" cannot pass for "faithful quotes".
- Every sentence must cite an id, which may make the model attach weak citations to transitions; the eval measures support against the whole article, as in v1, so a weak citation is not counted as an error by itself.
- A small window remains between sending a message and writing `sent_at`: a crash there sends the article twice (Telegram has no idempotency key). Documented, accepted.
- `drafts` stores page excerpts and generated text: the purge (R4) must cover it when it is designed.
- Deferred: worker concurrency (A2, R6: a second link waits for the first, about a minute), workflow state (A4), log correlation (A5), old-message replay (R7), health checks (R9), admin alerts (rest of R5).
