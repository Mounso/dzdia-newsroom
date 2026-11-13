# ADR-0011: A quote that fails validation is removed, not reworded

- **Status:** Accepted
- **Date:** 2026-11-13
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (points 3 and 4, and the second consequence)
- **Related:** [independent review of 2026-11-13](../reviews/2026-11-13-independent-review.md), finding V-1

## Context

[ADR-0009](0009-v2-link-to-sourced-article-slice.md) checks every quote in code: its text must be an exact substring of the evidence item it cites. A quote that still fails after the retry was to be rendered as indirect speech (`Selon <speaker>, <text>`, without quotation marks) and flagged in the sources message.

The independent review (finding V-1) showed that this fallback defeats the guard, before any of it was coded:

- a quote fails because its words are not in the evidence; the fallback keeps those same words, attributes them to a named person, and only removes the quotation marks;
- in the v1 baseline, 5 of the 7 unsupported quotes of `page_text` are rewordings of 11 to 20 words: exactly the text the fallback would keep;
- the eval would not see them: the extractor recognizes a quote by its quotation marks, so a downgraded quote leaves the quote count. Counting downgrades is not checking them;
- first-person text reads wrong in indirect speech ("Selon X, mon père…").

## Decision

1. **A failed quote is removed** (replaces the last sentence of ADR-0009, point 3). A quote that still fails the substring check after the retry is removed from the article. A paragraph left with no segment is removed; an article left with no paragraph fails the job with `GenerationFailedError`.
2. **After the retry, quote errors remove the quote** (replaces "quote errors fall back to indirect speech" in ADR-0009, point 4). The retry message tells the model that it may either copy the words exactly from the cited evidence, or restate the fact as a `sentence` segment with evidence ids. Indirect speech therefore still exists, but it is written by the model as an ordinary cited sentence, which the eval measures like any other sentence.
3. **The sources message gives the number of removed quotes, and nothing else about them**: "1 citation retirée : texte introuvable dans la source." Neither the speaker nor the rejected text is shown.
4. **The eval counts quotes kept and quotes removed per article** (replaces the second consequence of ADR-0009), so that "no quotes" cannot pass for "faithful quotes".

The validation-and-retry loop stays Abdou's kata; this ADR changes its specification, not who writes it.

## Alternatives considered

- **Keeping the indirect-speech fallback** (ADR-0009 as accepted): publishes the text the guard has just rejected, and moves it out of the eval's sight.
- **Failing the job on a failed quote**: strict, but one reworded quote would cost the journalist the whole article, and the baseline has 7 unsupported quotes out of 13 with the page text in the prompt.
- **Showing the speaker in the sources message**: more useful to the journalist, but the speaker comes from the model and is not verified (review finding V-2).
- **Showing the rejected text in the sources message**: lets the journalist check by hand, but puts unverified words one copy-paste away from the article.

## Consequences

- An article can lose a quote, and with it a paragraph, without a human seeing which one. The count in the sources message is the only trace; the journalist goes back to the source.
- Removing a segment can leave a rough transition in a paragraph. Accepted for this slice; the removed-quote count in the eval will show how often it happens.
- Whether the speaker of a kept quote is the right one is still unchecked by code. It is measured by hand in the v2 eval ([ADR-0010](0010-v2-comparison-protocol.md), point 7).
- The specification tests of the retry loop must cover: a quote fixed at the retry, a quote turned into a sentence at the retry, a quote removed, a paragraph emptied, an article emptied.
