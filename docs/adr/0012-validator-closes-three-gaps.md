# ADR-0012: The article validator removes a quote with an unknown id, and refuses quotation marks outside quotes and empty drafts

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (point 4: the list of errors and what happens after the retry)
- **Related:** [ADR-0011](0011-failed-quote-is-removed.md) (a failed quote is removed), [ADR-0010](0010-v2-comparison-protocol.md) (the eval that reads the validator counters)

## Context

[ADR-0009](0009-v2-link-to-sourced-article-slice.md), point 4, lists three errors: an unknown evidence id, a sentence with no id, and a quote that is not a substring of its evidence. After the single retry, id errors fail the job; since [ADR-0011](0011-failed-quote-is-removed.md), quote errors remove the quote.

Writing the specification tests of the validation-and-retry loop (before its code exists) showed three cases that these rules decide badly or not at all:

- **A quote that cites an unknown id.** Read literally, it is an id error and fails the job. But ADR-0011 rejected "failing the job on a failed quote" because one bad quote would cost the journalist the whole article, and a quote with a wrong id is a bad quote: it cannot be checked, and removing it is as safe as removing a reworded one.
- **Quotation marks in a `sentence` segment or in the title.** ADR-0009, point 3, says "the model never writes quotation marks", but only the prompt asks for it. A sentence such as `X a déclaré : « … ».` passes validation and is rendered as written, with words no code has checked. This is the v1 defect the slice exists to remove (reworded words inside quotation marks), the v2 eval's extractor would count it as a v2 quote, and news headlines often carry a quote. The spike of 2026-11-13 saw no case in 13 generations.
- **An empty draft.** `{"title": "…", "paragraphs": []}` is valid for the schema and breaks none of the three rules, so it would be returned as a validated article. ADR-0011 only covers an article emptied by a removal.

## Decision

1. **A quote that cites an unknown id is a quote error.** New issue kind `quote_unknown_id`. The retry message says that the id does not exist. A quote that still cites an unknown id after the retry is removed and counted with the removed quotes (ADR-0011, points 1, 3 and 4 apply as they are). `unknown_id` now concerns sentences only. A quote gets at most one issue: `quote_unknown_id`, else `quote_not_in_evidence`.
2. **Quotation marks outside a quote segment are an error.** New issue kind `quotation_marks`: the text of a `sentence` segment, or the title, contains one of `« » “ ” „ "`. Apostrophes are not quotation marks. The retry message tells the model to move the words to a `quote` segment or to drop the marks. Still there after the retry: the job fails with `GenerationFailedError`, like a sentence with no id.
3. **A draft with no segment is an error.** New issue kind `empty`: no paragraph, or only paragraphs with no segment. It uses the retry; still empty after it, the job fails. An empty paragraph next to non-empty ones is not an error.
4. **The rule after the retry, restated.** Issues about a quote segment (`quote_not_in_evidence`, `quote_unknown_id`) remove that quote; any other issue left (`unparsable`, `unknown_id`, `no_id`, `quotation_marks`, `empty`) fails the job. If both kinds are left, the job fails.

The validation-and-retry loop stays Abdou's kata; this ADR changes its specification (docstrings and tests in `tools/article.py` and `tests/tools/test_article_kata.py`), not who writes it.

## Alternatives considered

- **A quote with an unknown id fails the job** (ADR-0009 read literally): nothing to change, but it contradicts the reason ADR-0011 gives for removing a failed quote instead of failing.
- **Reporting it as `quote_not_in_evidence`**: no new kind, but the retry message would say the words are not in an evidence item that does not exist.
- **Leaving quotation marks to the prompt**: no case in the spike, but "quote fidelity is guaranteed by code" would be false at the first case, and ADR-0010 makes that guarantee the only claim left if the main comparison shows no difference.
- **Removing the sentence that contains quotation marks**: fewer failed jobs, but a cited fact disappears without a trace, and it needs a second counter and a second line in the sources message.
- **Checking each quoted span against the cited evidence** (a span between quotation marks must be a verbatim substring): keeps titles of works in guillemets, but it brings back the detection of quotes by pattern in free text, which ADR-0009 rejected as brittle. To reconsider if the ban costs too many titles.
- **`min_length=1` in the schema for the empty draft**: no new rule, but `remove_failed_quotes` builds a draft with no paragraph and leaves the failure to its caller, and the SDK's support for the constraint is unchecked.

## Consequences

- The quote guarantee can be stated in one sentence: every quotation mark in a v2 article was written by code, around words found verbatim in the cited evidence, or belongs to such a passage.
- Titles of works lose their guillemets (`le film La Bataille d'Alger`): a cost for a cultural outlet in plain text, accepted for this slice.
- A model that keeps quotation marks after the retry fails the whole article. How often the rule fires is measured on pages outside the sample before the prompts are frozen (ADR-0010, point 4); ADR-0010's limit of two failed v2 generations out of 16 still applies.
- Not covered: single quotation marks (`‘ ’`), because `’` is the French apostrophe; and the `speaker` field of a quote, which the model writes and no code checks (it is labelled by hand in the v2 eval, ADR-0010, point 7).
- The validator counters of the v2 eval have three more kinds to report.
