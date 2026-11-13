# ADR-0010: Comparison protocol for the v2 eval: full labels, a plain-prompt arm, and a conclusion fixed in advance

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0001](0001-baseline-hallucination-protocol.md) (point 4: the `v2` condition is defined and a `plain_prompt` condition is added; point 5: full labeling for this comparison)
- **Related:** [ADR-0008](0008-baseline-extractor-model.md) (extractor), [ADR-0009](0009-v2-link-to-sourced-article-slice.md), [ADR-0011](0011-failed-quote-is-removed.md) and [ADR-0012](0012-validator-closes-three-gaps.md) (the pipeline under test); [independent review of 2026-11-13](../reviews/2026-11-13-independent-review.md), findings V-2, V-3, V-4, V-5, V-6, V-10, E-3, E-5

## Context

Stage 2 measures the v2 slice against the v1 baseline. The line to beat is not `url_only` but `page_text / article`: going from `url_only` to `page_text` only shows the effect of downloading the page, and the contribution of v2 is what it adds beyond that. On the 16 original articles, `page_text / article` has 7 % [5–11 %] unsupported elements (19/256), and 13 articles of 16 contain at least one ([report](../../evals/datasets/baseline_v1/report.md)).

The independent review found that the v1 protocol, reused as it is, would give a result that cannot be defended:

- **The metric favors v2 by construction (V-4).** Unlabeled `found` elements count as supported, and only 20 % of them are labeled. In `page_text`, 4 of 83 audited `found` elements were not supported. v2 moves even more of its output into the `found` bucket, quotes first: they are exact substrings by construction.
- **The speaker of a quote is unchecked (V-2).** Code verifies the words. A quote given to the wrong person is `CONTRADICTED` in the labeling guide, and nothing would see it.
- **"Only the pipeline differs" is not accurate (V-5).** Between `page_text` and v2, the prompt, the temperature and structured output all change together, and the v1 prompt asks for an "engaging" article that "highlights talents and culture", which invites additions. A plain prompt may win most of the gain with no architecture.
- **The main rate may not move (V-3).** 12 of the 19 unsupported elements are not quotes, and v2 addresses them only through its prompt. With 0 to 3 unsupported elements per article over 16 articles, a modest real gain may not separate from zero.
- **The labeler was not blind (E-5).** The v1 sheet showed the condition and the matcher's verdict, and the only labeler is the project's author.

This protocol is fixed before any v2 code runs on the eval set.

## Decision

1. **Scope: the article task, on the 16 original articles.** Summaries and the opaque duplicates are out of the comparison (v2 writes articles only; the duplicates exist only in `url_only`).
2. **Three arms, one generation per article and per arm**, all with `gemini-3.1-flash-lite-preview`:
   - `page_text`: the existing v1 runs (`v1-frozen` prompt, model defaults). They are reused, not generated again.
   - `plain_prompt`: the v1 pipeline (free text out, no evidence ids, no structured output, no validator) with a new prompt `v1-plain-1`. That prompt carries the same editorial instructions as `v2-article-1`, including "use only the facts stated in the text", and the same generation settings as v2.
   - `v2`: the pipeline of ADR-0009 as amended by ADR-0011, prompt `v2-article-1`.
3. **Same input text for every arm.** `plain_prompt` and `v2` receive exactly the frozen snapshot text that `page_text` received and that the labeler reads, with the same 30,000-character cap. v2 builds its evidence from that text: nothing is fetched or extracted again for the eval.
4. **Prompts are frozen before the run.** `v2-article-1` and `v1-plain-1` are written and tried on pages outside the sample. The 16 articles are then run once. A generation is repeated only after a technical failure (timeout, API error), and the repeat is recorded. A prompt changed after the run gets a new version and a new arm; both are reported.
5. **Extraction and matching are unchanged** (ADR-0008 settings, same extractor prompt, same matcher). The extractor receives the rendered article, title included, without the sources message and without the `[n]` markers. The existing `page_text` extractions are reused.
6. **Every element of the three arms is labeled by hand**; the 20 % audit of `found` elements does not apply to this comparison. The existing labels of `page_text / article` are kept; its remaining `found` elements (about 185) are labeled with the new arms. Labels are never pre-filled or corrected by an LLM.
7. **Quotes are labeled for their speaker.** The guide's rules stay: reworded words are `C`, a quote given to someone else is `C`. A `C` given for the wrong speaker carries `locuteur` in `note`, so the report can count misattributed quotes separately. No code guard on the speaker is added in this slice; the measured count decides whether one is needed.
8. **Blind labeling.** The sheet hides the arm, the matcher's verdict and its score. Rows are grouped by article and shuffled across arms inside each article, with a fixed seed. Each row has an opaque key; the key-to-element mapping stays in `evals/.data/`. The labeling guide is frozen before the export; a rule changed afterwards is stated in the report. Any mechanical consistency check run on the labels is a committed script.
9. **Primary result: the paired difference `v2 − page_text` of the unsupported-element rate.** Rates are pooled over articles, as in the v1 report. The 95 % interval comes from a cluster bootstrap that resamples articles and takes the same articles in both arms.
10. **Secondary results, reported next to the primary one, with the same intervals where a rate is involved:**
    - the two halves of the difference: `plain_prompt − page_text` (prompt and settings) and `v2 − plain_prompt` (evidence ids, structured output, validator);
    - per arm: the rate without quotes, the contradicted rate, the articles with at least one unsupported element, the number of elements and the article length;
    - quotes per arm: stated and unsupported; for v2 also kept, removed (ADR-0011) and misattributed;
    - validator counters for v2, with no labeling: unknown ids, sentences without an id and failed quotes at the first pass, retries used, generations that failed.
11. **What will be written, decided now:**
    - the interval of the primary difference is entirely below zero: "v2 lowers the unsupported rate by X points [interval] on the article task";
    - the interval contains zero: "no detectable difference on 16 articles", with the estimate and its interval. The write-up and the CV line then claim only what code guarantees and the labels confirm (the wording of kept quotes, with the misattribution count), not a lower overall rate;
    - the interval is entirely above zero: v2 is worse; this is reported, and the cause is investigated before any real use.
    - In every case, a gain is attributed to the architecture only for the part `v2 − plain_prompt`. If `plain_prompt − page_text` carries most of it, the write-up says that the prompt did.
12. **Guards on the comparison itself.**
    - A v2 generation that fails is counted and reported. Paired differences use the articles that have an output in both arms. With more than 2 failures out of 16, no headline comparison is published until the cause is understood.
    - If an arm states fewer than two thirds of the elements of `page_text`, the write-up gives that difference next to its rate: writing vaguely lowers the rate without being more faithful.
13. **Temperature is verified, not assumed.** ADR-0009 sets temperature 0 for v2. Google's guide says recent models ignore `temperature`; this is unconfirmed for `gemini-3.1-flash-lite-preview`. A few calls outside the sample check it before the run. The settings actually sent are recorded with each output, and the report says whether temperature had an effect.

## Alternatives considered

- **Keeping the 20 % audit in both arms**: about 45 minutes of labeling instead of 4 hours, but the comparison would rest on the matcher exactly where the arms differ most.
- **Full labels for v2 only**: half the work, but two arms measured two ways.
- **No ablation**: no answer to "would a better prompt have been enough?".
- **A "v2 without validator" arm instead of `plain_prompt`**: isolates the validator, for the same labeling cost, but answers a narrower question. The validator counters of point 10 cover most of it without labels.
- **The rate without quotes as the primary result**: more honest about what the prompt alone brings, but harder to read, and quotes are where v1 fails most. It is reported as a secondary result.
- **A larger sample**: more power, but new v1 runs and new labels, beyond the 2026-11-22 target.
- **A non-blind sheet, as in v1**: nothing to code, but the labeler grades his own system knowing which arm he reads.
- **An LLM judge for the extra labels**: fast, but uncalibrated; human labels are the reference the judge will be calibrated against.

## Consequences

- Labeling grows from about 70 rows to about 700 (roughly 185 for `page_text`, 250 for each new arm), about 4 hours for Abdou. `plain_prompt` is the arm to drop if time runs out; the attribution rule of point 11 then cannot be applied, and the write-up says so.
- The comparison no longer depends on the matcher, and `page_text / article` becomes a rate instead of a floor. The v1 report would change if it were rebuilt with the new labels: how the v1 figures are presented is decided with the review's findings E-1 and E-2, not here.
- Blinding is partial: the style of a v2 article can reveal its arm, and the 71 existing `page_text` labels were given unblinded.
- The harness needs: the `plain_prompt` and `v2` conditions, a blind export and its import, full-label export for the article task, the paired bootstrap and the comparison report.
- Known limits that stay: one labeler; 16 articles of general news from two outlets; the metric sees entities and quotes only, so an unsupported claim with no name or number is invisible in every arm; the new arms are extracted later than `page_text` by a preview model that may have changed.
