# ADR-0016: The v1 baseline is reported per task, with the article task fully labeled and every other rate printed as a floor

- **Status:** Accepted
- **Date:** 2026-11-14
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0001](0001-baseline-hallucination-protocol.md) (point 5, step 3: the 20 % sample; point 6: how a rate is reported)
- **Related:** [ADR-0010](0010-v2-comparison-protocol.md) (full labels on `page_text / article`), [ADR-0013](0013-v2-comparison-harness.md) (the comparison's own labels file); [independent review of 2026-11-13](../reviews/2026-11-13-independent-review.md), findings E-1, E-2, E-3, E-4 and E-12; [baseline results](../evals/baseline-v1.md#results)

## Context

The baseline page leads with one figure: in `url_only`, at least 29 % [21–36 %] of elements are unsupported (74/258). The independent review found three defects in how it is presented, and the figures confirm them.

| Cell (16 original articles) | Elements | Unsupported, known | `found` elements not labeled | What the figure is |
| --- | --- | --- | --- | --- |
| `url_only` / article | 126 | 59 (47 %) | 49 | a floor |
| `url_only` / summary | 132 | 15 (11 %) | 101 | a floor |
| `page_text` / article | 256 | 33 (12.9 %) | 0, with the labels of the v2 comparison | a rate |
| `page_text` / summary | 189 | 1 (1 %) | 140 | a floor |

- **The figure is a floor printed with a two-sided interval (E-1).** ADR-0001 has a human label every `not_found` element and a 20 % sample of the `found` ones; the other `found` elements count as supported. A reader takes 36 % as the upper limit of the truth. It is not: the interval is the interval of the floor.
- **The floor can be far from the rate.** On `page_text / article`, the floor was 6 % (15/256); once the v2 comparison had every element labeled, the rate was 12.9 % [8.5–18.1 %] (33/256). On `url_only / summary`, 8 of the 22 audited `found` elements are unsupported, so the 11 % floor is well under the truth. On `url_only / article`, 1 of the 13 audited ones is.
- **The figure is a blend (E-2).** It pools 48 summaries and 16 articles: 11 % on summaries, 47 % on articles. Three summaries are generated per article, so the blend moves with that setting. The product writes articles.
- **"Multiplies by about 8" compares two rates with different denominators (E-3).** A `url_only` article states 7.9 checkable elements on average, a `page_text` article 16.0: a vaguer text is less exposed.
- **The slug result rests on four articles (E-4).** The report prints 70 % [46–88 %] against 22 % [9–39 %]. A bootstrap over four clusters is not a 95 % interval, and one article carries 13 of the 21 unsupported elements of the opaque arm.
- **The README is out of date (E-12):** it says the baseline is in progress and counts 20 articles.

## Decision

1. **The article task leads.** The figure quoted first, in the baseline page, the roadmap and the README, is `url_only / article` next to `page_text / article`. The summary task is given beside it. The rows for all tasks together stay in the report's table, named as a blend that depends on the number of summaries per article, and are not quoted as the result.
2. **Every element of `url_only / article` is labeled.** The 49 `found` elements that the 20 % sample left out are labeled by the same labeler, with the baseline's guide, against the frozen copy. `export-labels` gets an option that adds the unlabeled `found` elements of one condition and task to the sheet; the labels go to the baseline's labels file like the others. The other three cells keep the 20 % sample.
3. **A rate is printed as a rate only when its population is fully labeled.** In the generated report and in the docs:
   - a population with no unlabeled `found` element prints its rate with its 95 % interval, as today;
   - a population that still has unlabeled `found` elements prints a floor: `≥ X % (k/n)`, followed by the lower end of its bootstrap interval only, with no upper end;
   - the rule applies to every table of the report: elements, contradicted elements, outputs with at least one unsupported element, rates by type;
   - each row of the main table gives its number of unlabeled `found` elements, and the matcher audit is given per condition and task.
4. **`page_text / article` is quoted from the v2 comparison**: 12.9 % [8.5–18.1 %] (33/256), every element labeled on a blind sheet. The baseline's report does not read the comparison's labels file, so its own row for this cell stays a floor, and the baseline page says where the rate comes from.
5. **No ratio between two rates.** The docs give the two rates, their counts and the number of elements per output. "Multiplies by" is not written.
6. **The slug result is given in counts.** For the four articles also reached through an opaque URL, the report prints the unsupported and total elements of each article through both URLs, the pooled counts, and in how many of the four the share rises. It prints no interval. These counts are floors too. The docs say that the share rises in that many articles of four, and that four articles are too few to quantify the effect.
7. **What may be written**, fixed before the 49 labels exist:
   - "Given only the URL, v1 writes articles in which X % [L–U %] of the checkable elements are unsupported by the source (k/126). Given the page text, the same prompt leaves 12.9 % [8.5–18.1 %] unsupported (33/256)."
   - For summaries: "at least 11 % (15/132) against at least 1 % (1/189)", with the audit result of each cell.
   - `page_text` is the old prompt given the text, not a new system: the pair is never written as a before and after of the rewrite. The result of v2 is the one of the [v2 comparison](../evals/v2-comparison.md).
   - The earlier figures (28 %, then 29 %, for both tasks together) stay in the page as the first presentation, with the reason they were replaced.
8. **The README is updated** with the true status of the project and this result (E-12).
9. **No CV wording is committed.** The repository is public; that wording is kept outside it.

## Alternatives considered

- **Keeping the blend first, with the two tasks under it**: less to rewrite and continuity with what was already said, but the first figure a reader sees still depends on the number of summaries per article.
- **Both tasks side by side, with no leading figure**: nothing is favored, but there is no single sentence to quote, and the product's task is the article.
- **No new label, the floor printed with its lower limit and a hard ceiling** (between 47 % and 86 % for `url_only / article`): nothing to label, but the main pair stays a floor against a rate, and the ceiling is too far to inform.
- **The audit-weighted estimate with its wide interval** (the review computed 36 % [23–53 %] for both tasks, on the first reading): no new label, but on the article task it rests on 13 audited elements, one of them unsupported.
- **Labeling the 150 unlabeled `found` elements of `url_only`, summaries included**: every `url_only` figure becomes a rate, for 101 more rows on the task the product does not ship. It can be done later with the same option.
- **The baseline report reads the comparison's labels file**: one report would hold both rates and a paired difference, but the baseline would depend on a later harness that was built to read it and never write it (ADR-0013).
- **A ratio computed on counts over the same articles**: avoids the denominator problem, but one side is a floor until the labels are in, and a count ratio still hides that the two arms do not state the same number of elements.
- **Rewording the slug result in the docs only**: no code change, but the generated report, which is committed and public, would keep an interval over four articles.
- **A one-sided 95 % bound for the floors**: the right bound for "at least", but a second kind of interval in one report; the lower end of the existing interval is slightly more cautious and needs no new computation.

## Consequences

- **49 rows to label, and they are not blind.** The sheet holds one cell only, so the labeler knows that every row is a `url_only` article element that the code found in the source. It is the same single labeler, after the results were seen. The 77 elements of that cell already labeled were not blind either (review, E-5).
- **The two rates of the main pair come from two labeling passes.** `url_only / article` is labeled with the baseline's guide and its sheet; the 182 elements of `page_text / article` added by the comparison were labeled blind, with the comparison's guide. The comparison page already notes that the earlier, non-blind labels may be more lenient.
- **The headline can only go up.** A `found` element labeled unsupported adds to the 59 known ones: the rate will lie between 47 % and 86 %, and the audit (1 of 13) suggests it stays near the floor.
- **The other three cells stay floors**, and the report now says so on each row instead of in one sentence under a table.
- **The report changes shape.** Its figures do not change, except the cell that gets labels; the slug rows lose their intervals. The report of 2026-11-14 stays in the Git history.
- **No difference with an interval is given for the main pair.** The two rates are stated side by side; a paired difference would need the baseline to read the comparison's labels.
- **Sixteen articles from two outlets on one day.** The per-task figure does not widen the sample: the limits of the baseline page still apply.
- To update when this ADR is accepted: the status line of ADR-0001, the index, `docs/evals/baseline-v1.md`, `docs/roadmap.md` and `README.md`.
