# Baseline v1 — hallucination report

Models: gemini-3.1-flash-lite-preview · prompts: v1-frozen · protocol: ADR-0001, reported per ADR-0016

Unsupported = CONTRADICTED + NOT_IN_SOURCE, 95 % CI from a bootstrap over articles. *Output rate* = share of outputs with at least one unsupported element. A range `low–high [CI]` bounds a rate while elements are pending; the CI spans the low bound's lower end to the high bound's upper end. *Undetermined* outputs have pending elements and no unsupported one yet. A cell written `≥ X% (k/n), CI from L%` is a floor: its population still has *unlabeled found* elements, which count as supported, so the true rate can only be higher; it has no upper end, and L is the lower end of the floor's own CI. A rate with its full CI is printed only for a population whose `found` elements are all labeled. The `all tasks (blend)` rows pool the summary and article outputs: they move with the number of summaries generated per article and are not a result to quote. Original items only; the opaque duplicates are in *Slug effect*.

| Condition / task | Outputs | Refusals | Elements | Pending | Unlabeled found | Undetermined outputs | Unsupported elements | Contradicted (decided) | Outputs with ≥1 unsupported |
|---|---|---|---|---|---|---|---|---|---|
| url_only / summary | 48 | 0 | 132 | 0 | 101 | 0 | ≥ 11% (15/132), CI from 5% | ≥ 5% (6/132), CI from 0% | ≥ 25% (12/48), CI from 10% |
| url_only / article | 16 | 0 | 126 | 0 | 0 | 0 | 57% [48%–67%] (72/126) | 6% [1%–11%] (7/126) | 94% [81%–100%] (15/16) |
| url_only / all tasks (blend) | 64 | 0 | 258 | 0 | 101 | 0 | ≥ 34% (87/258), CI from 25% | ≥ 5% (13/258), CI from 2% | ≥ 42% (27/64), CI from 30% |
| page_text / summary | 48 | 0 | 189 | 0 | 140 | 0 | ≥ 1% (1/189), CI from 0% | ≥ 1% (1/189), CI from 0% | ≥ 2% (1/48), CI from 0% |
| page_text / article | 16 | 0 | 256 | 0 | 182 | 0 | ≥ 6% (15/256), CI from 4% | ≥ 2% (5/256), CI from 0% | ≥ 75% (12/16), CI from 50% |
| page_text / all tasks (blend) | 64 | 0 | 445 | 0 | 322 | 0 | ≥ 4% (16/445), CI from 2% | ≥ 1% (6/445), CI from 0% | ≥ 20% (13/64), CI from 14% |

## Unsupported rate by element type (original items)

Over decided elements, per condition and task.

| Condition / task | Type | Pending | Unlabeled found | Unsupported |
|---|---|---|---|---|
| url_only / summary | person | 0 | 32 | ≥ 2% (1/42), CI from 0% |
| url_only / summary | organization | 0 | 17 | ≥ 10% (2/21), CI from 0% |
| url_only / summary | place | 0 | 46 | ≥ 17% (10/60), CI from 5% |
| url_only / summary | number | 0 | 3 | ≥ 0% (0/3), CI from 0% |
| url_only / summary | date | 0 | 3 | ≥ 33% (2/6), CI from 0% |
| url_only / summary | quote | 0 | 0 | n/a |
| url_only / article | person | 0 | 0 | 25% [0%–52%] (5/20) |
| url_only / article | organization | 0 | 0 | 31% [0%–59%] (5/16) |
| url_only / article | place | 0 | 0 | 71% [55%–84%] (48/68) |
| url_only / article | number | 0 | 0 | n/a |
| url_only / article | date | 0 | 0 | 25% [0%–100%] (1/4) |
| url_only / article | quote | 0 | 0 | 72% [52%–100%] (13/18) |
| page_text / summary | person | 0 | 16 | ≥ 0% (0/20), CI from 0% |
| page_text / summary | organization | 0 | 23 | ≥ 0% (0/32), CI from 0% |
| page_text / summary | place | 0 | 64 | ≥ 1% (1/87), CI from 0% |
| page_text / summary | number | 0 | 28 | ≥ 0% (0/36), CI from 0% |
| page_text / summary | date | 0 | 9 | ≥ 0% (0/13), CI from 0% |
| page_text / summary | quote | 0 | 0 | 0% [0%–0%] (0/1) |
| page_text / article | person | 0 | 29 | ≥ 3% (1/39), CI from 0% |
| page_text / article | organization | 0 | 24 | ≥ 3% (1/35), CI from 0% |
| page_text / article | place | 0 | 60 | ≥ 7% (6/87), CI from 3% |
| page_text / article | number | 0 | 40 | ≥ 0% (0/44), CI from 0% |
| page_text / article | date | 0 | 25 | ≥ 0% (0/39), CI from 0% |
| page_text / article | quote | 0 | 4 | ≥ 58% (7/12), CI from 22% |

## Matcher audit

Unlabeled 'found' elements count as supported in the tables above, which is why a row with unlabeled 'found' elements gives floors. *Confirmed supported* is the share of the labeled 'found' elements of a cell that a human confirmed as SUPPORTED; when every 'found' element of a cell is labeled, its line is a full count, not a sample.

| Condition / task | found elements | Labeled | Confirmed supported |
|---|---|---|---|
| url_only / summary | 123 | 22 | 64% [35%–91%] (14/22) |
| url_only / article | 62 | 62 | 77% [64%–89%] (48/62) |
| page_text / summary | 182 | 42 | 100% [100%–100%] (42/42) |
| page_text / article | 223 | 41 | 98% [92%–100%] (40/41) |

## Slug effect

Original articles also reached through an opaque `?p=ID` URL, `url_only` outputs of all tasks. Counts only.

| Article | Normal URL: unsupported / elements | Normal URL: unlabeled | Opaque URL: unsupported / elements | Opaque URL: unlabeled | Share rises |
|---|---|---|---|---|---|
| tsa-00 | 2/7 | 2 | 3/6 | 3 | yes |
| tsa-01 | 2/15 | 6 | 2/2 | 0 | yes |
| tsa-02 | 3/16 | 9 | 3/7 | 4 | yes |
| tsa-05 | 8/16 | 6 | 13/15 | 2 | yes |
| All | 15/54 | 23 | 21/30 | 9 | — |

The share of unsupported elements rises in 4 of the 4 articles where both URLs gave elements. These counts are floors: an element with no label counts as supported when `found`, and a pending `not_found` one is not counted as unsupported either. No interval is printed: 4 articles are too few to quantify the effect.
