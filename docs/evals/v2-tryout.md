# v2 prompt tryout

Before the v2 eval runs on the 16 sampled articles, its two new prompts are tried on pages outside the sample and then frozen ([ADR-0010](../adr/0010-v2-comparison-protocol.md), point 4). This page describes that tryout: what it runs, how the pages are chosen, and the rule that decides between freezing and revising.

The tryout is not a measurement. It uses 6 pages and mechanical counters only: nothing is labeled, and no rate is reported from it.

## What is tried

| Arm | Prompt | Pipeline |
| --- | --- | --- |
| `v2` | `v2-article-1` and the retry message (`src/dzdia_newsroom/tools/article.py`) | Evidence ids, structured output, validation, one retry, removal of failed quotes ([ADR-0009](../adr/0009-v2-link-to-sourced-article-slice.md), [ADR-0011](../adr/0011-failed-quote-is-removed.md), [ADR-0012](../adr/0012-validator-closes-three-gaps.md)) |
| `plain` | `v1-plain-1` (`evals/v2/arms.py`) | Free text out, no evidence ids, no validator: the ablation arm of ADR-0010 |

Both arms use the same model, temperature 0, the same editorial instructions and the same input text, capped once at 30,000 characters. The two arm functions in `evals/v2/arms.py` take one capped text and return one output; the eval on the frozen sample reuses them unchanged.

## Pages

Six pages: the three pages of the earlier spike, plus three picked mechanically from the most recent entries of the two baseline feeds. The three picks are the candidate with the most quoted passages, the longest one and the one with the most numbers, so that quotes, the evidence cut and figures are exercised. Two picks come from Algérie 360 and one from TSA, which gives three pages per outlet.

A page is refused when:

- its URL, or its final URL after redirects, is the URL of a sampled article (`sample.jsonl`, `snapshots.jsonl`);
- its feed date is not after the most recent article of the sample.

The command also prints the sample's titles next to the retained pages, so that a follow-up of a sampled story can be spotted by a human. The retained list is committed in `evals/datasets/v2_tryout/pages.jsonl` (metadata and text hashes, no article text). Page texts and model outputs stay in `evals/.data/v2_tryout/`.

## Commands

```bash
uv run python -m evals.v2.tryout select --include <spike url> <spike url> <spike url>
uv run python -m evals.v2.tryout run
uv run python -m evals.v2.tryout counters
```

On Windows, add `-X utf8` after `python`: the steps print French titles, which the default console encoding cannot always encode.

- `select` downloads the candidates, applies the guards above, writes the snapshots and the page list. It refuses to overwrite an existing list without `--force`.
- `run` checks each snapshot against its recorded hash, then runs both arms. It is idempotent: a run is identified by page, arm, prompt fingerprint and model, and a run that succeeded is skipped.
- `counters` prints the per-page counters, the totals and the verdict. It prints no article text.

Each record stores the model, the date, the prompt version, the settings actually sent and a fingerprint of the prompt text. The version name stays `-1` during the tryout because the prompts have never run on the sample; the fingerprint is what tells one revision round from the next.

## Freeze rule

The rule was fixed before the first call. The prompts are frozen when all five conditions hold on the six pages:

1. no failed v2 generation;
2. `quotation_marks` at the first attempt on at most one page;
3. the retry used on at most two pages;
4. at most one quote removed in total;
5. the median length of the v2 articles is at least two thirds of the median length of the plain ones.

Otherwise the prompts are revised and the six pages run again, for at most two rounds. If the conditions still fail after the second round, there is no third round: the validator rule on quotation marks (ADR-0012) is reopened instead.

One failed generation out of six would predict about three out of sixteen, above the limit of two that ADR-0010 sets for publishing a comparison. Six pages cannot prove the opposite: no failure out of six is still compatible with a true failure rate of up to about 39 %. The tryout is a smoke test, not a guarantee.

## Reading the flagged segments

When the verdict is `REVISE`, the prompt is revised from the segments that the validator flagged, not from the whole drafts:

```bash
uv run python -m evals.v2.tryout issues
```

The step prints, for each attempt, the kind of each issue, its position, the id involved and the flagged text. It prints nothing else of the model output, and only the length of an unparsable answer. Reading whole drafts would tune the prompt to the reader's taste rather than to the errors.

## Results

Round 1, 2026-11-13, `gemini-3.1-flash-lite-preview`, temperature 0. Prompt fingerprints: `b9e5672bfd4d` (`v2-article-1`), `5026941ef6b3` (`v1-plain-1`). Twelve runs, no technical error.

| Page | Picked for | v2 outcome | Retry | Issues, attempt 1 | Issues, attempt 2 | Quotes kept | Quotes removed | v2 chars | Plain chars |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tryout-00` | spike | ok | no | none | | 1 | 0 | 1,114 | 1,593 |
| `tryout-01` | spike | ok | no | none | | 1 | 0 | 781 | 1,105 |
| `tryout-02` | spike | ok | no | none | | 0 | 0 | 981 | 1,305 |
| `tryout-03` | quotes | ok | yes | 1 `quote_not_in_evidence` | none | 3 | 0 | 1,370 | 1,426 |
| `tryout-04` | length | ok | no | none | | 0 | 0 | 1,715 | 2,291 |
| `tryout-05` | numbers | ok | no | none | | 0 | 0 | 1,159 | 1,663 |

| Condition | Observed | Result |
| --- | --- | --- |
| No failed v2 generation | 0 failed | met |
| `quotation_marks` at the first attempt on at most one page | 0 pages | met |
| Retry used on at most two pages | 1 page | met |
| At most one quote removed in total | 0 removed | met |
| Median v2 length at least two thirds of the plain one | 1,136.5 against 1,509.5 (0.75) | met |

Verdict: **freeze**, with no revision round.

## Freeze

`v2-article-1` and `v1-plain-1` are frozen as they ran in round 1. Their fingerprints are pinned in `evals/v2/arms.py`, and `tests/evals/test_v2_arms.py` fails when either changes.

The v2 fingerprint covers everything the model reads besides the evidence: the prompt text, the JSON schema of the draft with its field descriptions, and every line of the retry message. The plain fingerprint covers the prompt text. To change a frozen prompt: give it a new version, run the tryout again, then update the pin. A prompt changed after the eval run is a new arm, and both are reported (ADR-0010, point 4).

The v2 fingerprint includes the schema as pydantic renders it, so an upgrade of pydantic can change it without any edit of the prompt. The test then fails, which is the intended signal: the model reads that schema.

What round 1 shows, and what it does not:

- The retry ran against the real model for the first time, once. The quote that failed at the first attempt was fixed by the retry, and nothing was removed.
- The rule on quotation marks outside quote segments (ADR-0012) never fired.
- The v2 articles are shorter than the plain ones on every page, by a quarter at the median. The eval reports the number of elements per arm, and ADR-0010 (point 12) says what to write if an arm states far fewer elements.
- Three of the six pages contain no quoted passage, so quotes were exercised on three pages only.
- Nothing here says whether the articles are faithful: that is what the labeled eval measures.
