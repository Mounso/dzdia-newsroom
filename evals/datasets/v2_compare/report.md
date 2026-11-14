# v2 comparison — unsupported elements on the article task

Protocol: ADR-0010; harness: ADR-0013. Unsupported = CONTRADICTED + NOT_IN_SOURCE. A verdict is a human label only: an element without a label is pending. An arm with a pending label gets no rate, no per-type count, no difference and no conclusion.

## Settings

| Arm | Model | Prompt version | Prompt fingerprint | Settings sent | Extractor |
|---|---|---|---|---|---|
| page_text | gemini-3.1-flash-lite-preview | v1-frozen | — | model defaults | gemini-3.1-pro-preview (low) |
| plain_prompt | gemini-3.1-flash-lite-preview | v1-plain-1 | 5026941ef6b3613555b545cb171134451dd373bec7936809edf398e94c62df7b | temperature=0.0 | gemini-3.1-pro-preview (low) |
| v2 | gemini-3.1-flash-lite-preview | v2-article-1 | b9e5672bfd4df0fddd15534c0c539ba25f1af9029b6b522c38f67f6034738de6 | response_schema=ArticleDraft, temperature=0.0 | gemini-3.1-pro-preview (low) |

Intervals: percentile cluster bootstrap over articles, 10,000 resamples, seed 0, alpha 0.05 (95 %), index convention of the v1 report.

Label sheet exports:
- 2026-11-13 22:46 UTC: seed v2-compare-1, 603 rows, guide SHA-256 06724af425dfc9026c5654f6b436cd68f797d78e52047196a07105e9eb189865
Current labeling guide SHA-256: 06724af425dfc9026c5654f6b436cd68f797d78e52047196a07105e9eb189865

## Status

| Arm | Articles with an output | v2 generations failed validation | Technical errors open | Elements | Labeled | Pending |
|---|---|---|---|---|---|---|
| page_text | 16 of 16 | — | 0 | 256 | 256 | 0 |
| plain_prompt | 16 of 16 | — | 0 | 227 | 227 | 0 |
| v2 | 16 of 16 | 0 | 0 | 194 | 194 | 0 |

## Results per arm

Shown for an arm once every element of its articles with an output has a label.

| Arm | Unsupported | Unsupported without quotes | Contradicted | Articles with ≥1 unsupported | Elements | Median article length (chars) | Elements against page_text |
|---|---|---|---|---|---|---|---|
| page_text | 12.9 % [8.5–18.1 %] (33/256) | 10.2 % [6.2–15.3 %] (25/244) | 2.7 % [0.7–5.9 %] (7/256) | 93.8 % [81.2–100.0 %] (15/16) | 256 | 3504.5 | — |
| plain_prompt | 0.0 % [0.0–0.0 %] (0/227) | 0.0 % [0.0–0.0 %] (0/227) | 0.0 % [0.0–0.0 %] (0/227) | 0.0 % [0.0–0.0 %] (0/16) | 227 | 1455.5 | 88.7 % (227/256 on 16 articles) |
| v2 | 2.6 % [0.0–6.3 %] (5/194) | 1.7 % [0.0–4.1 %] (3/178) | 2.1 % [0.0–5.7 %] (4/194) | 18.8 % [0.0–37.5 %] (3/16) | 194 | 1305.5 | 75.8 % (194/256 on 16 articles) |

## Quotes

Misattributed: a quote labeled CONTRADICTED whose note contains `locuteur`. Kept and removed: by the validator of v2 (ADR-0011), from the runs.

| Arm | Stated | Unsupported | Misattributed | Kept | Removed |
|---|---|---|---|---|---|
| page_text | 12 | 66.7 % [33.3–100.0 %] (8/12) | 1 | — | — |
| plain_prompt | 0 | n/a | 0 | — | — |
| v2 | 16 | 12.5 % [0.0–40.0 %] (2/16) | 2 | 16 | 1 |

## Differences of the unsupported rate

Paired over the articles with an output in both arms; each resample takes the same articles in both.

| Difference | Estimate [95 % interval] | Paired articles | Resamples skipped |
|---|---|---|---|
| v2 − page_text (primary) | −10.3 points [−15.3; −5.8] | 16 | 0 |
| plain_prompt − page_text | −12.9 points [−18.1; −8.5] | 16 | 0 |
| v2 − plain_prompt | +2.6 points [0.0; 6.3] | 16 | 0 |

A gain is attributed to the architecture only for the part v2 − plain_prompt; the part plain_prompt − page_text comes from the prompt and the generation settings (ADR-0010, point 11).

## Conclusion

v2 lowers the unsupported rate by 10.3 points [5.8; 15.3] on the article task.

## Validator counters of v2

No label involved. Over the 16 v2 generations without a technical error.

| Counter | Count |
|---|---|
| First-attempt issues: unparsable | 0 |
| First-attempt issues: unknown_id | 0 |
| First-attempt issues: no_id | 0 |
| First-attempt issues: quote_not_in_evidence | 2 |
| First-attempt issues: quote_unknown_id | 0 |
| First-attempt issues: quotation_marks | 0 |
| First-attempt issues: empty | 0 |
| Runs that used the retry | 2 |
| Generations that failed validation | 0 |
| Quotes kept | 16 |
| Quotes removed | 1 |

## Label checks

| Date | Labels checked | Supported quotes not found | Same value, different labels | `locuteur` outside a contradicted quote |
|---|---|---|---|---|
| 2026-11-14 14:07 UTC | 603 | 0 | 30 | 0 |
| 2026-11-14 14:35 UTC | 603 | 0 | 30 | 1 |
| 2026-11-14 14:39 UTC | 603 | 0 | 30 | 0 |

Elements whose label value changed in the comparison's labels file: 1.

## Limits

- With 16 articles, a percentile bootstrap interval tends to be somewhat too narrow.
- One labeler, the project's author.
- The 74 earlier page_text labels were given unblinded, under the v1 labeling guide.
- The new arms were extracted later than page_text, by a preview model that may have changed.
- The metric sees entities and quotes only: an unsupported claim with no name, number, date or quote is invisible in every arm.
