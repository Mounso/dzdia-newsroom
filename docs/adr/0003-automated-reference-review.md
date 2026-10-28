# ADR-0003: Automated reference review, headlines included, paywalled articles excluded

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0001](0001-baseline-hallucination-protocol.md) (points 2 and 3)

## Context

[ADR-0001](0001-baseline-hallucination-protocol.md) planned for Abdou to review the 16 reference texts by hand. Abdou chose to delegate this review. Claude replaced it with a systematic check: for each article, the precision-oriented extraction is compared with a more permissive one (`favor_recall`) to spot lost paragraphs, and the text is searched for boilerplate (subscription, cookies, "lire la suite"). This check found two defects:

1. `tsa-07` was a **paywalled** article, truncated by the "Abonnez-vous… pour lire la suite" call to action. Its reference was therefore incomplete.
2. **No reference contained the headline**, even though it carries facts (names, numbers). It is precisely what v1 guesses from the URL slug.

## Decision

- Paywalled articles are excluded: `extract_main_text` raises `PaywallError` when the HTML contains a paywall marker. `tsa-07` is replaced by the next feed entry at sampling time, `tsa-08` (`?p=304027`).
- The RSS headline is placed at the top of every reference text.
- The 16 texts are marked `reviewed` after the automated check, which no longer reports any defect.

## Alternatives considered

None recorded in the original decision.

## Consequences

- **Nobody has read the texts.** A subtler extraction error remains possible, for example a paragraph missing from both extraction modes. We will compensate during labeling: every `NOT_IN_SOURCE` element is checked on the website itself before being confirmed.
- `tsa-07` was not the original of an opaque duplicate, so the 4 duplicates do not change.
- Paywalled articles bias the sample toward free content. This is acceptable: the bot will not be able to read paywalled articles anyway.
