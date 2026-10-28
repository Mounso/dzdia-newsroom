# ADR-0002: Algérie 360 replaces El Watan in the baseline sample

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0001](0001-baseline-hallucination-protocol.md) (point 2)

## Context

On 2026-10-27, `elwatan.dz` resets every connection, whether with curl, urllib or httpx, on `/feed/` as well as on `/rss`. We therefore cannot draw a sample from it. We tested French-language replacements:

- **Algérie 360**: 25 articles, general news, short `?p=ID` links;
- **Algérie Eco**: 10 articles, economy only;
- **L'Expression, Le Soir d'Algérie, El Moudjahid**: 403 error.

## Decision

The 8 El Watan articles are replaced by the 8 most recent **Algérie 360** articles. The opaque duplicates are still drawn from TSA.

## Alternatives considered

- **Algérie Eco**: its topical focus is too narrow; the sample would be less representative.
- **Working around El Watan's block** (posing as a browser, going through a proxy): contrary to the site's intent, and fragile.

## Consequences

- Algérie 360 publishes many briefs and articles taken from other outlets. The texts will be shorter than El Watan's, so there will be fewer critical elements per article.
- The sample loses a major daily newspaper of record. To be reconsidered if El Watan becomes reachable again before labeling.
- Algérie 360 inserts « À LIRE AUSSI » links to other articles inside its article bodies: 20 lines across the 8 articles. These lines are removed by `clean_text` (covered by a test), starting again from the stored HTML. Otherwise, facts unrelated to the article would be counted as "found" in the reference.
