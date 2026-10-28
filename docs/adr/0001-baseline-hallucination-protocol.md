# ADR-0001: Measurement protocol for the v1 hallucination baseline

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amended by:** [ADR-0002](0002-algerie-360-replaces-el-watan.md) (point 2), [ADR-0003](0003-automated-reference-review.md) (points 2 and 3)

## Context

The v1 bot passes the raw URL to the LLM and never downloads the page: the URL text stands in for `content` in both `get_summary` and `get_article`. We want to put a number on this defect before fixing it, then measure v2 on exactly the same sources.

## Decision

1. **What we measure: faithfulness to the source**, not real-world accuracy. An element that is true but absent from the source article counts as a hallucination. The unit of measurement is the **critical element**: person, organization, place, number, date or quote. These elements are collected from the summary and from the article, without splitting opinion or style sentences. Two metrics:
   - the **rate of unsupported elements**, by type and overall;
   - the **rate of outputs containing at least one unsupported element**.
2. **Sample: 16 articles + 4 duplicates.**
   - The 16 articles are the most recent ones in the RSS feeds of TSA Algérie and El Watan, 8 per outlet.
   - The 4 duplicates point to the same TSA article through an opaque URL (`?p=ID`), which isolates the effect of the slug without adding anything to label.
3. **Snapshot of each page**: the raw HTML, the text extracted by trafilatura and then **reviewed by hand**, the fetch date and a SHA-256 hash. The labeling reference is the reviewed text. These snapshots stay out of Git (copyright); the repository only keeps the metadata and the hashes.
4. **Execution.**
   - The v1 prompts are copied verbatim (`v1-frozen`), with the v1 model and default parameters (`gemini-3.1-flash-lite-preview`). If that model has been retired, we use the closest successor and say so in the report.
   - 3 runs for the summary, 1 for the article.
   - Three conditions are compared:
     - `url_only`: v1 as it is;
     - `page_text`: the v1 prompts with the actual page text;
     - `v2`: later.

     The opaque duplicates only go through `url_only`, since their text is identical to that of the original article.
5. **Layered labeling.**
   1. An LLM extracts the critical elements, as structured output.
   2. Code looks for each element in the source: exact match after normalization for numbers, dates and quotes; approximate match over word windows (rapidfuzz, adjustable threshold) for names.
   3. A human labels everything the code did not find, plus a 20 % sample of what it found, in order to measure the code's precision. The possible labels are `SUPPORTED`, `CONTRADICTED` and `NOT_IN_SOURCE`.
   4. An LLM judge may run in parallel on the same elements. Its verdicts are stored separately to calibrate the judge in week 5, and never count in the result.
6. **Reporting the result**: every rate is given with a 95 % confidence interval obtained by bootstrap, resampling articles (not elements), because the elements of a single article are not independent.

## Alternatives considered

- **A binary score per output, and nothing else.** It saturates around 100 % for v1, and moving to element granularity later would mean relabeling everything.
- **A "random" sample without a defined starting list.** This is really a convenience sample, and therefore biased.
- **The LLM judge as the only ground truth.** It is not calibrated: we could not defend its numbers.
- **Exact matching everywhere.** Every transliteration variant would end up in human review.

## Consequences

- **Language:** the sample is entirely in French, for lack of an Arabic-language source. Arabic will be evaluated later.
- **Size:** 20 URLs give wide intervals. We show this rather than hide it.
- **LLM extraction:** the extractor can miss elements, and our numbers cannot see those misses. The human therefore checks the extracted list during labeling.
- **False positives from the code:** an element can be "found" in the source while it appears there in a different context. The 20 % sample measures this bias.
- **El Watan availability:** `elwatan.dz` was dropping connections (TCP reset) on 2026-10-27. If this persists, it will be replaced by another French-language source, with a new ADR.
