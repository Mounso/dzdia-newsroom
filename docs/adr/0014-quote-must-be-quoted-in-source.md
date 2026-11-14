# ADR-0014: A quote is kept only when the source itself puts its words inside quotation marks

- **Status:** Accepted
- **Date:** 2026-11-14
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (point 3: the quote check), [ADR-0011](0011-failed-quote-is-removed.md) (point 3: the wording of the removed-quote count), [ADR-0012](0012-validator-closes-three-gaps.md) (point 4: the list of quote errors; the first consequence: the guarantee sentence)
- **Related:** [ADR-0010](0010-v2-comparison-protocol.md) (point 7 left this guard to the measured count; point 4 on prompt versions), [ADR-0013](0013-v2-comparison-harness.md) (the comparison command that gains a replay step); [independent review of 2026-11-13](../reviews/2026-11-13-independent-review.md), finding V-2; [comparison results](../evals/v2-comparison.md#labels-and-report-2026-11-14)

## Context

The v2 validator checks that the text of a quote segment is an exact substring of the evidence item it cites (ADR-0009, point 3). Code then prints it as `<speaker> : « <words> »`. ADR-0010, point 7, added no code guard on the speaker and left that decision to the count measured by the comparison.

The comparison of 2026-11-14 measured it. Of the 16 quotes that v2 kept on the 16 articles, 2 are labeled `C` with `locuteur`: in both, the model copied a sentence of the journalist's own narration, word for word, into a quote segment and gave it a speaker. The words are in the evidence, so the validator accepted them. These two quotes and their two speakers are 4 of the 5 unsupported elements of v2.

A candidate rule was replayed offline on the recorded outputs, with no model call: accept a quote only when its passage lies inside quotation marks in the evidence item it cites.

| Kept v2 quotes, 16 articles | Count | Labels |
| --- | --- | --- |
| Passage inside quotation marks in its evidence item | 11 | 11 `S` |
| Passage outside quotation marks | 5 | 2 `C` (the two cases above), 3 `S` |

- **The rule rejects both false quotes.**
- **It also rejects 3 quotes labeled `S`.** In the source they are indirect speech (a reporting verb, then the words, with no quotation marks). The labeling guide judges the words and the speaker against the source, and both are right; but v2 prints as someone's exact words a passage that the source's journalist reports in his own sentence.
- Articles with at least one quote would go from 9 to 6 of 16.
- On the six tryout pages (outside the sample, unlabeled), 1 of the 5 kept quotes lies outside quotation marks.
- On the 22 frozen pages (16 sampled, 6 tryout): 344 evidence items, 41 with quotation marks, none with unbalanced marks, none with straight double quotes. 11 pages have no quotation mark at all, and 7 % of the characters lie inside quotation marks.

One other rule was replayed and rejected: requiring the speaker's name in the cited item or its neighbours. It rejects both false quotes, but also 2 quotes labeled `S` on the sample and 3 of 5 on the tryout pages, where the source names the speaker further away or with another wording.

## Decision

1. **A quote is kept only when the source presents its words as quoted.** The passage that `check_quote` finds in the cited evidence item must lie entirely inside quotation marks in that item. The rule is checked by code and stated in the prompt. It applies to evidence built from a third-party web page, the only input of this slice (ADR-0009).
2. **What "inside quotation marks" means.**
   - Quotation depth is computed on the text of the cited evidence item alone, with the function that already keeps evidence cuts out of quotations (`_quote_depths` in `tools/evidence.py`: `«` and `“` open, `»` and `”` close, `"` toggles).
   - The depth must be above zero before the first character of the passage, after its last one and everywhere in between. A passage that joins two quoted spans across the journalist's own words is therefore refused.
   - When the passage occurs several times in the item, one occurrence inside quotation marks is enough, and that occurrence is the one rendered.
   - A quotation left open at the end of an item counts as open: the French layout that reopens `«` at the start of each paragraph of a long quote is accepted.
3. **A new issue kind, `quote_not_quoted_in_source`.** A quote still gets at most one issue, in this order: `quote_unknown_id`, `quote_not_in_evidence`, `quote_not_quoted_in_source`.
4. **It follows the path of the other quote errors** (ADR-0011, ADR-0012, point 4). The retry message says that the source does not put these words inside quotation marks and asks for the fact as a `sentence` segment with its evidence ids. A quote that still fails after the retry is removed and counted with the removed quotes. Code never rewrites a quote as indirect speech.
5. **The prompt becomes `v2-article-2`.** The quote rule of the format instructions, the description of the quote text in the schema and the retry message say that only words inside quotation marks in the source may go in a `quote` segment, and that reported speech without quotation marks is written as a `sentence`. `v2-article-1` stays the prompt of the published comparison; its records keep their version and fingerprint.
6. **The removed-quote count has one wording for every cause** (replaces the sentence quoted in ADR-0011, point 3): "N citation(s) retirée(s) : non confirmée(s) par la source." Still no speaker and no rejected text.
7. **Measurement: a replay and a tryout, no new labeled arm.**
   - A committed step, `python -m evals.v2.compare guard-replay`, applies the validator to the recorded v2 drafts of the comparison and prints counters only: per article, the quotes kept by `v2-article-1`, those the new rule rejects, and the labels of that article's v2 quotes. It calls no model and writes nothing. Its figures go in the comparison page, named as a replay.
   - `v2-article-2` is tried on the six tryout pages before it is frozen, with the freeze rule of the first tryout unchanged (its five conditions, at most two revision rounds). The counters also give `quote_not_quoted_in_source` at the first attempt. The plain arm is not run again. If the conditions still fail after the second round, this ADR is reopened.
   - The 16 sampled articles are not generated again and no row is labeled.
8. **What may be written.**
   - The comparison of 2026-11-14 stays the published result, for `v2-article-1`. No unsupported rate is stated for `v2-article-2`.
   - The replay is stated as what it is: on the recorded drafts, the rule rejects 5 of 16 kept quotes, the 2 labeled misattributed and 3 labeled supported that the source gives as indirect speech.
   - The guarantee of ADR-0012 becomes: every quotation mark in a v2 article was written by code, around words that the cited evidence itself puts inside quotation marks.
   - The speaker is still written by the model and checked by no code.

## Alternatives considered

- **The code check without the prompt rule**: the same guarantee, but the model learns the rule only from the retry message, so more retries and more removed quotes.
- **The prompt rule without the code check**: nothing to add to the validator, but nothing is guaranteed, and its effect can only be known from a new labeled run.
- **No guard, the count documented as a limit**: no work before the stage 2 target, but 2 of 16 quotes print a journalist's sentence as a named person's words, which is the kind of error the slice exists to remove.
- **Requiring the speaker's name near the passage**: aims at the speaker itself, but name matching failed on real pages (see Context).
- **Accepting indirect speech after a reporting verb**: keeps the 3 supported quotes, but it needs a list of verbs and a parse of French syntax, and it still prints reported words as exact ones.
- **Code turns a refused quote into `Selon <speaker>, <words>`**: keeps the content, but the speaker is the unverified field, so the two false quotes would stay false attributions without quotation marks. ADR-0011 rejected the same fallback.
- **Rendering quotes without their speaker**: removes the unverified field from the output, but a journalist's sentence would still be printed between quotation marks, and a quote with no speaker is of little use.
- **Quotation depth computed over the whole page**: follows a quote that continues in a paragraph with no opening mark, but one unbalanced mark would flip every item after it.
- **A new labeled arm on the 16 articles**: a rate for `v2-article-2`, but the rule was designed by reading the errors of these 16 articles, so that rate would be measured on its own design set, for about 200 more rows to label.
- **A new sample of pages**: the only clean measure, but new snapshots, runs and labels, beyond the 2026-11-22 target.
- **A second model call that judges the speaker**: could catch a right quote given to the wrong person, but it is an uncalibrated judge, one more call per article, and no labeled set exists to calibrate it.

## Consequences

- **Fewer quotes.** On the recorded drafts, 5 of 16 would go, and 3 articles would lose every quote. A page with no quotation mark gets no quote, whatever its content: 11 of the 22 frozen pages. An interview laid out as questions and answers, with no quotation marks, would get none either.
- **The rule fits a press page, not a first-person source.** A social media post or an interview transcript is its author's speech from end to end and carries no quotation marks, so this rule would refuse every quote taken from it. When such an input is added, its quote rule is decided then; there the speaker can come from the source's metadata instead of the model. This matters for the target use: of the 200 most recent articles of dzdia.com (read on 2026-11-14), 71 embed an Instagram post and 138 carry at least one quoted passage of 60 characters or more, and at least 112 of those 529 passages are followed by an attribution to the outlet's own interview.
- **The pipeline that ships is no longer the arm that was measured.** They differ by this rule, which can only refuse a quote; but after a refusal the model writes a new sentence in its place, and that text is unmeasured. The write-up says so.
- **The speaker is still unchecked.** A passage that the source does quote, given to the wrong person, passes. No such case was seen among the 11 quotes inside quotation marks; 11 is too few to say it is rare.
- **Not every quoted passage is speech.** A title of a work, a nickname or an expression between quotation marks in the source can be printed as a quote with a speaker.
- **Known gaps of the mark detection**, accepted: a quote that continues in a paragraph with no opening mark is refused; an unbalanced mark or a stray `"` flips the depth for the rest of its item; `„` and single marks (`‘ ’`) are not counted. None occurs in the 344 items of the 22 pages, which come from two outlets.
- **A new prompt version**: a new fingerprint and pin, six real v2 generations on the tryout pages (which need their own go-ahead), and the freeze recorded in the tryout page. The comparison's `run` step keeps its records and generates nothing again.
- The specification tests must cover: a passage inside marks; outside marks; joining two quoted spans; two occurrences, one inside; a quotation left open at the end of the item; the issue order; the retry message line; removal after the retry.
- To update when this ADR is accepted: the status lines of ADR-0009, ADR-0011 and ADR-0012, the index, `docs/evals/v2-comparison.md` and `docs/evals/v2-tryout.md`.
