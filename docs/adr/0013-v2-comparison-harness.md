# ADR-0013: The v2 comparison runs in its own command, with a blind sheet, its own labels file and intervals fixed in advance

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0010](0010-v2-comparison-protocol.md) (the protocol this harness implements), [ADR-0001](0001-baseline-hallucination-protocol.md) and [ADR-0008](0008-baseline-extractor-model.md) (the baseline pipeline it reuses), [ADR-0003](0003-automated-reference-review.md) (the live-page check of the v1 labeling guide, not applied here)

## Context

[ADR-0010](0010-v2-comparison-protocol.md) fixes what the v2 comparison measures and what will be written from it. It leaves the harness open: where the two new arms live, how the blind sheet is built and read back, which bootstrap gives the interval of the paired difference, and how temperature is checked before the run.

Facts that weigh on these choices, measured on 2026-11-13:

- The baseline's `elements.jsonl` holds 733 elements, and 252 human labels point at them by id. The file is in `evals/.data/` (not in Git), and the `extract` and `match` steps rewrite it whole. A fault there cannot be undone from the repository, and a new extraction by a preview model may not give the same elements.
- `page_text / article` has 256 elements on the 16 original articles. 74 are labeled; the 182 others are all `found`.
- No label of the baseline carries the note `absent du snapshot`: the live-page rule of the v1 guide was never used.
- The `page_text` articles are 3,517 characters long at the median. In the prompt tryout, on other pages, the v2 articles were 1,136.5 characters and the plain ones 1,509.5. The new arms will probably state far fewer than two thirds of the elements of `page_text`, so the second guard of ADR-0010, point 12, is expected to apply, and the sheet should hold fewer rows than the 700 that ADR-0010 estimated.

All of the points below were decided before any output of the new arms existed on the sample.

## Decision

1. **A separate command.** The comparison lives in the package `evals/v2/compare/`, run as `python -m evals.v2.compare <step>`, with the steps `run`, `extract`, `match`, `export-labels`, `import-labels`, `check-labels` and `report`.
   - Its local files are in `evals/.data/v2_compare/` (runs, extractions, elements, the sheet, the key mapping); its committed files are in `evals/datasets/v2_compare/` (labels, export log, report).
   - It reads the baseline's files and never writes them: the sample, the snapshot hashes and texts, and the runs, extractions, elements and labels of `page_text / article`.
   - It imports the baseline's functions unchanged: `extract_run`, the matcher, the label parser, the bootstrap. The only change in `evals/baseline_v1/` is the `Condition` type, which gains `plain_prompt` and `v2`; the baseline command and `plan_jobs` keep accepting `url_only` and `page_text` only.
2. **The `run` step.**
   - One record per article and arm, with the id `<item_id>:<condition>:article:0`. The record follows the tryout's: model, date, prompt version and fingerprint, settings sent, hash and length of the input text, tokens, latency, and for v2 the outcome, the attempts with their issues, the quotes kept and removed, the draft and the rendered text.
   - It refuses to start when a prompt fingerprint differs from its pin, or when a snapshot text does not match its recorded hash. The input is `cap_text` of the snapshot text, the same for both arms.
   - A generation that succeeded is never run again: the step has no `--force`. A technical error (API, timeout) is recorded and retried by running the step again; the error record stays in the file and the report counts it. A v2 generation that fails validation is a result, not an error: it is recorded and never retried.
   - It prints counters only, never model text, so the labeler does not see the outputs before labeling.
3. **Extraction and matching.** The text given to the extractor is the plain output, or the rendered text of v2. The extractor settings and the matcher are the baseline's. The elements of the new arms go to `evals/.data/v2_compare/elements.jsonl`; those of `page_text` are read from the baseline.
4. **The blind sheet.**
   - Rows: every element of the two new arms, plus the unlabeled elements of `page_text / article` on the 16 original articles. The 74 labeled ones are not shown again (ADR-0010, point 6).
   - Columns: `key; item_id; type; value; context; label; note`. The arm, the run id, the element id (which contains the arm), the matcher's verdict and its score are absent.
   - The key is the first 10 hexadecimal characters of the SHA-256 of a fixed seed and the element id. The export fails on a collision. The mapping from key to element id is written to `evals/.data/v2_compare/`.
   - Order: articles in the order of the sample; inside an article, rows sorted by key. The key is a seeded hash, so this is a fixed shuffle across arms that does not change when the sheet is exported again.
   - One CSV file in the baseline's format. Rows left blank stay pending, so the sheet can be imported in several sittings. An export writes only the rows that have no label yet, and refuses to overwrite a sheet holding labels that were not imported.
   - Each export appends a line to a committed log: date, seed, number of rows per article (all arms together), and the SHA-256 of the labeling guide.
5. **A separate labels file.** `import-labels` writes to `evals/datasets/v2_compare/labels.jsonl`, the new `page_text` labels included. The baseline's `labels.jsonl` does not change, so the v1 report can be rebuilt as published; how the v1 figures are presented with the new labels stays with review findings E-1 and E-2. The comparison reads both files.
6. **The labeling guide of the comparison** is `docs/evals/v2-labeling-guide.md`, a file of its own so that its hash changes only when a rule does. It is frozen before the first export. It keeps the rules of the v1 guide and the speaker rule of ADR-0010, point 7, with two changes:
   - **Elements are judged against the snapshot text only.** The v1 rule "search the live page before choosing `N`; if the element is there, label `S` with `absent du snapshot`" is dropped. The three arms received the snapshot text and nothing else, so an element that is on the website but not in the snapshot did not come from the input.
   - The rule "note a fact that the extractor missed on a row of the same run" is dropped: the labeler no longer sees the outputs or the runs.
7. **Mechanical checks on the labels** are one committed step, `check-labels`, with three checks fixed now. It lists rows by key, never by arm:
   - a quote labeled `S` that the matcher does not find in the snapshot;
   - rows of one article with the same type and the same normalized value but different labels;
   - `locuteur` in the note of a row that is not a quote labeled `C`.

   A flagged row is read again by the labeler, who keeps or changes his label in the sheet and imports it again. The write-up gives the number of rows flagged and changed.
8. **Intervals.** Percentile cluster bootstrap over articles, 10,000 resamples, seed 0, with the index convention of the v1 report.
   - A rate of one arm resamples the articles that have an output in that arm.
   - A difference between two arms resamples the articles that have an output in both, and each resample takes the same articles in both arms. A resample where an arm has no element is skipped, and the report says how many were.
   - An article with an output and no element counts as (0, 0).
   - No second test and no other kind of interval is computed.
9. **The report.**
   - It shows no rate, no difference and no conclusion for an arm that still has unlabeled elements: only its counts and the number pending. There is no interim look at partial results.
   - The sentence of ADR-0010, point 11, is chosen by code from the interval of `v2 − page_text`: entirely below zero (upper end < 0), entirely above zero (lower end > 0), or containing zero.
   - The two guards of ADR-0010, point 12, are applied by code: with more than 2 failed v2 generations, the report states the guard and no conclusion; an arm that states fewer than two thirds of the elements of `page_text`, on the articles they share, gets that difference written next to its rate.
   - A misattributed quote is a quote labeled `C` whose note contains `locuteur`.
   - The report holds counts, rates and settings only, no model text; it is committed as `evals/datasets/v2_compare/report.md`.
10. **Temperature is checked by a committed step,** `python -m evals.v2.tryout determinism`, on two tryout pages, the first of each outlet in the page list. With the frozen prompts: the plain arm three times at temperature 0 and three times at 1, and the v2 arm three times at 0. Each call records the hash and the length of its output, and the step prints the number of distinct outputs per page, arm and temperature. The result does not block the eval run: it is written in `docs/evals/v2-tryout.md` and repeated in the comparison write-up (ADR-0010, point 13).

## Alternatives considered

- **Adding the two conditions to `evals/baseline_v1`**, with the same runs, elements and labels files: nothing to duplicate, but the baseline's steps rewrite `elements.jsonl` whole, the new `page_text` labels would change the published v1 report, and its `run` step would by default run every condition.
- **A new command that writes into the baseline's files**: one set of files, with the risk of the first alternative and the code of the decision.
- **Grouping rows of the same value inside an article**: faster to label and still blind, but it invites copying a verdict down a group without reading each context, which is the error the guide warns about for `found` rows, and it departs from the shuffle of ADR-0010, point 8.
- **Random keys or row numbers kept only in a mapping file**: equally opaque, but a lost mapping would lose the link to the labels; a seeded hash can be computed again.
- **Keeping the live-page rule**: consistent with the v1 guide, but it would label `S` an element that the model did not get from its input, in a comparison whose point is faithfulness to that input.
- **A sign-flip permutation test next to the bootstrap**: a second view of the same difference, but two figures that can disagree when the rule for the conclusion is already fixed on one of them.
- **A BCa interval**: better coverage in theory, but unstable with 16 clusters, more code, and not the method of the v1 figures it is compared to.
- **Provisional rates with bounds while labels are pending**, as in the v1 report: useful for one arm, but bounds on a difference are wide and hard to read, and partial results invite looking before the labels are complete.
- **A throwaway script for the temperature check**, as in the spike: nothing to maintain, but the result could not be reproduced.

## Consequences

- The baseline is read-only for this work: its report and its 252 labels stay as published.
- The labels of `page_text / article` are split across two files until E-1 and E-2 are decided.
- The key is a hash of a committed seed and the element id, so it can be reversed by anyone who computes it over the elements. Blinding protects against bias while labeling, not against an effort to look; `labels.jsonl` and the mapping also reveal the arm and must stay closed during labeling.
- With 16 articles, a percentile interval tends to be somewhat too narrow. The report states it as a limit; the method is the same as for the v1 figures.
- The 74 earlier `page_text` labels were given under the v1 guide. None used the live-page rule, so the rule change does not make them inconsistent with the new ones; they remain unblinded (ADR-0010, Consequences).
- While any label is missing in an arm, the report gives nothing for it. If `plain_prompt` is dropped for lack of time, the two halves of the difference are absent and the write-up says so (ADR-0010, Consequences).
- The code is written in three parts, each reviewed before the next: the `run` step and the temperature check; extraction, matching, the blind sheet and the checks; the report, which is written while labeling goes on and without access to the labels.
