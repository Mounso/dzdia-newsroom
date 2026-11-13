# ADR-0008: Gemini 3.1 Pro (preview, low thinking) extracts the elements of the v1 baseline

- **Status:** Accepted
- **Date:** 2026-11-09
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0001](0001-baseline-hallucination-protocol.md) (point 5.1: an LLM extracts the critical elements)

## Context

[ADR-0001](0001-baseline-hallucination-protocol.md) has an LLM list the critical elements of each v1 output, without naming the model. The default, `gemini-2.5-flash`, could not finish on the free tier (20 requests per day: 20 extractions out of 144, set aside by B5). Two candidates were already ruled out:

- `gemini-3.5-flash-lite`, tried on the same 20 outputs: it found 76 % of the distinct elements, but 2 quotes out of 7 and 8 organizations out of 16. Missed elements are invisible to the metrics, so it would have understated hallucinations.
- `gemini-3.1-flash-lite-preview`, the model under test: it would extract from its own outputs.

Constraint: the baseline figure must be final by 2026-11-11. Abdou enabled billing on the Gemini API project (tier 1, prepaid credit of 5 €). Only Gemini is implemented (`StructuredGenerator` in `evals/baseline_v1/llm.py`); another provider needs a new client.

The job is small: 144 outputs, about 154,000 characters. A probe on the longest output of each condition and task (4 outputs, both thinking levels) measured:

| Thinking level | Thinking tokens (4 outputs) | Elements found | Latency | Extrapolated cost for 144 outputs |
|---|---|---|---|---|
| `low` | 691 to 1,379 | 9, 3, 21, 5 | 7 to 13 s | about 2 $ |
| default (`high`) | 1,531 to 5,893 | 7, 3, 24, 5 | 11 to 39 s | about 5 $ |

Thinking tokens are billed as output (12 $ per million tokens for this model, prompts under 200k tokens). The default level gave no better recall on these outputs (-2 and +3 elements on the two articles, equal on the summaries).

## Decision

1. **Extractor: `gemini-3.1-pro-preview`**, the most capable Gemini model available on the paid tier (tier 1 limits: 25 requests per minute, 250 per day).
2. **Thinking level `low`**, temperature 0, structured output as before. Settings: `EXTRACTION_MODEL` and `EXTRACTION_THINKING_LEVEL` (default `low`); the thinking level applies to the extractor only, never to the v1 model under test, which keeps its default parameters ([ADR-0001](0001-baseline-hallucination-protocol.md)).
3. **All 144 outputs are extracted with this one model and level, in one pass.** Extraction records keep the model name; the report states the model, the thinking level and the run date.

## Alternatives considered

- **`gemini-2.5-flash` on the paid tier**: nothing to change, but its own recall was never measured, and from 2026-12-01 Google routes this id to `gemini-3.5-flash-lite`, the model already ruled out.
- **Default thinking (`high`)**: about 2.5 times the cost (close to the whole 5 € credit, with no margin for retries) and 3 times the latency, for no measured gain in recall.
- **Claude through the API**: another model family, so no shared bias with the model under test. It needs a new client and tests, and API credits were uncertain: half a day to a day, too much risk for the deadline. Kept for the v2 evaluation.
- **Groq (`gpt-oss-120b`, free tier)**: another family and free, but a new client, free-tier limits and unknown reliability of its JSON output.
- **The Gemini free tier, a share every day**: about 7 days.

## Consequences

- **Same model family as the model under test.** The extractor judges nothing: code checks each element against the reference, then a human labels every `not_found` element and a 20 % sample of `found` ones, and checks the extracted list. The shared family can still affect which elements are listed; this limit is stated in the report.
- **Preview model**: its behavior can change without notice, so the extraction runs in one pass and is not mixed with later extractions.
- **Cost**: about 2 $ for the full run, plus the 0.25 $ probe.
- The tier-1 limits of this model (25 requests per minute) make a rate limiter in the client useful ([R8](../reviews/2026-10-28-design-review.md#2-robustness-and-optimizations)).
