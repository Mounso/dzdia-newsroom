# Baseline v1 — hallucination report

Models: gemini-3.1-flash-lite-preview · prompts: v1-frozen · protocol: ADR-001

Unsupported = CONTRADICTED + NOT_IN_SOURCE, 95 % CI from a bootstrap over articles. *Output rate* = share of outputs with at least one unsupported element. A range `low–high [CI]` bounds a rate while elements are pending; the CI spans the low bound's lower end to the high bound's upper end. *Undetermined* outputs have pending elements and no unsupported one yet.

| Condition / task | Outputs | Refusals | Elements | Pending | Undetermined outputs | Unsupported elements | Contradicted (decided) | Outputs with ≥1 unsupported |
|---|---|---|---|---|---|---|---|---|
| url_only / summary | 48 | 0 | 132 | 0 | 0 | 11% [5%–19%] (15/132) | 5% [0%–10%] (6/132) | 25% [10%–42%] (12/48) |
| url_only / article | 16 | 0 | 126 | 0 | 0 | 47% [38%–54%] (59/126) | 3% [0%–7%] (4/126) | 94% [81%–100%] (15/16) |
| url_only / all tasks | 64 | 0 | 258 | 0 | 0 | 29% [21%–36%] (74/258) | 4% [1%–7%] (10/258) | 42% [30%–56%] (27/64) |
| page_text / summary | 48 | 0 | 189 | 0 | 0 | 1% [0%–2%] (1/189) | 1% [0%–2%] (1/189) | 2% [0%–6%] (1/48) |
| page_text / article | 16 | 0 | 256 | 0 | 0 | 6% [4%–9%] (15/256) | 2% [0%–5%] (5/256) | 75% [50%–94%] (12/16) |
| page_text / all tasks | 64 | 0 | 445 | 0 | 0 | 4% [2%–6%] (16/445) | 1% [0%–3%] (6/445) | 20% [14%–27%] (13/64) |
| slug (originals) | 16 | 0 | 54 | 0 | 0 | 22% [9%–39%] (12/54) | 6% [0%–11%] (3/54) | 44% [25%–81%] (7/16) |
| opaque ?p=ID | 16 | 0 | 30 | 0 | 0 | 70% [46%–88%] (21/30) | 0% [0%–0%] (0/30) | 69% [50%–88%] (11/16) |

## Unsupported rate by element type (decided elements, all conditions)

| Condition | Type | Pending | Unsupported |
|---|---|---|---|
| url_only | person | 0 | 8% [0%–18%] (5/62) |
| url_only | organization | 0 | 14% [0%–27%] (5/37) |
| url_only | place | 0 | 38% [23%–54%] (48/128) |
| url_only | number | 0 | 0% [0%–0%] (0/3) |
| url_only | date | 0 | 30% [0%–100%] (3/10) |
| url_only | quote | 0 | 72% [52%–100%] (13/18) |
| page_text | person | 0 | 2% [0%–8%] (1/59) |
| page_text | organization | 0 | 1% [0%–6%] (1/67) |
| page_text | place | 0 | 4% [1%–8%] (7/174) |
| page_text | number | 0 | 0% [0%–0%] (0/80) |
| page_text | date | 0 | 0% [0%–0%] (0/52) |
| page_text | quote | 0 | 54% [22%–78%] (7/13) |

## Matcher audit

Precision of automatic 'found' (human-confirmed SUPPORTED): 91% [83%–97%] (108/119)

Unaudited 'found' elements count as supported, so every unsupported rate above is a lower bound when this precision is below 100 %. By condition (original items only):

- url_only: 74% [54%–93%] (26/35)
- page_text: 99% [96%–100%] (82/83)
