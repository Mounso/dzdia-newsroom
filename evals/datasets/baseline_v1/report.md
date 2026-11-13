# Baseline v1 — hallucination report

Models: gemini-3.1-flash-lite-preview · prompts: v1-frozen · protocol: ADR-001

Unsupported = CONTRADICTED + NOT_IN_SOURCE, 95 % CI from a bootstrap over articles. *Output rate* = share of outputs with at least one unsupported element. A range `low–high [CI]` bounds a rate while elements are pending; the CI spans the low bound's lower end to the high bound's upper end. *Undetermined* outputs have pending elements and no unsupported one yet.

| Condition / task | Outputs | Refusals | Elements | Pending | Undetermined outputs | Unsupported elements | Contradicted (decided) | Outputs with ≥1 unsupported |
|---|---|---|---|---|---|---|---|---|
| url_only / summary | 48 | 0 | 132 | 0 | 0 | 9% [4%–15%] (12/132) | 3% [0%–9%] (4/132) | 23% [8%–38%] (11/48) |
| url_only / article | 16 | 0 | 126 | 0 | 0 | 48% [39%–56%] (60/126) | 0% [0%–0%] (0/126) | 94% [81%–100%] (15/16) |
| url_only / all tasks | 64 | 0 | 258 | 0 | 0 | 28% [21%–35%] (72/258) | 2% [0%–4%] (4/258) | 41% [30%–53%] (26/64) |
| page_text / summary | 48 | 0 | 189 | 0 | 0 | 3% [1%–5%] (5/189) | 2% [0%–4%] (3/189) | 10% [2%–21%] (5/48) |
| page_text / article | 16 | 0 | 256 | 0 | 0 | 7% [5%–10%] (18/256) | 3% [1%–6%] (7/256) | 81% [62%–100%] (13/16) |
| page_text / all tasks | 64 | 0 | 445 | 0 | 0 | 5% [3%–7%] (23/445) | 2% [1%–4%] (10/445) | 28% [19%–38%] (18/64) |
| slug (originals) | 16 | 0 | 54 | 0 | 0 | 22% [10%–39%] (12/54) | 0% [0%–0%] (0/54) | 38% [25%–62%] (6/16) |
| opaque ?p=ID | 16 | 0 | 30 | 0 | 0 | 70% [46%–88%] (21/30) | 0% [0%–0%] (0/30) | 69% [50%–88%] (11/16) |

## Unsupported rate by element type (decided elements, all conditions)

| Condition | Type | Pending | Unsupported |
|---|---|---|---|
| url_only | person | 0 | 6% [0%–17%] (4/62) |
| url_only | organization | 0 | 22% [8%–39%] (8/37) |
| url_only | place | 0 | 35% [22%–51%] (45/128) |
| url_only | number | 0 | 0% [0%–0%] (0/3) |
| url_only | date | 0 | 30% [0%–100%] (3/10) |
| url_only | quote | 0 | 67% [47%–92%] (12/18) |
| page_text | person | 0 | 3% [0%–10%] (2/59) |
| page_text | organization | 0 | 1% [0%–6%] (1/67) |
| page_text | place | 0 | 6% [2%–10%] (10/174) |
| page_text | number | 0 | 2% [0%–8%] (2/80) |
| page_text | date | 0 | 4% [0%–10%] (2/52) |
| page_text | quote | 0 | 46% [20%–75%] (6/13) |

## Matcher audit

Precision of automatic 'found' (human-confirmed SUPPORTED): 92% [84%–97%] (109/119)

Unaudited 'found' elements count as supported, so every unsupported rate above is a lower bound when this precision is below 100 %. By condition (original items only):

- url_only: 86% [68%–100%] (30/35)
- page_text: 95% [88%–100%] (79/83)
