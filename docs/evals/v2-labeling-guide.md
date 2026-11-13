# Labeling guide of the v2 comparison

This guide is frozen before the first export of the blind sheet ([ADR-0013](../adr/0013-v2-comparison-harness.md), point 6). Its SHA-256 is recorded with each export. A rule changed after the first export is stated in the comparison report ([ADR-0010](../adr/0010-v2-comparison-protocol.md), point 8).

## What a row is

Each row of the sheet is one element stated in an article that one of three systems wrote from the same source text. The sheet does not say which system, and it carries no automatic verdict.

| Column | Content |
| --- | --- |
| `key` | Opaque id of the row. Do not change it. |
| `item_id` | The source article. |
| `type` | `person`, `organization`, `place`, `number`, `date` or `quote`. |
| `value` | The element, copied from the generated article. |
| `context` | The sentence of the generated article that states it. |
| `label` | To fill: `S`, `C` or `N`. |
| `note` | Free text, optional except where a rule below asks for it. |

Rows are grouped by source article and shuffled inside each article.

## The question

Open the reference text of the row's article, `evals/.data/baseline_v1/snapshots/<item_id>.txt`, and ask: **does this text state what `context` says about `value`?**

| The reference text... | Label |
| --- | --- |
| states the same thing about this element | `S` (SUPPORTED) |
| states something incompatible: another number, place, date, or a quote given to someone else | `C` (CONTRADICTED) |
| says nothing about it | `N` (NOT_IN_SOURCE) |

## Rules

1. **Judge against the reference text only.** Do not open the article's web page. An element that is true, or that is on the website but not in the reference text, is `N`: the three systems received the reference text and nothing else.
2. **Check the sense, not the string.** "Constantine" is `C` if the reference text places the event in Constantine and `context` places it in Oran.
3. **Search before choosing `N`.** The reference text may word the element differently ("algérien" for "Algérie"): look for synonyms and other spellings.
4. **A quote is a promise of the exact words.** A quote whose words are reworded is `C`, even with the same meaning and the right speaker. A quote whose words appear nowhere in the reference text, in any wording, is `N`.
5. **A quote is also judged for its speaker.** When `context` gives the words to a person other than the one who says them in the reference text, the label is `C` and `note` contains the word `locuteur`. This holds whether or not the words are exact. When `context` names no speaker, judge the words only.
6. **Doubtful cases** get the closest label and a reason in `note`.
7. **Each row is judged on its own.** Do not copy the label of another row with the same value without reading this row's `context`.
8. **Rows can be left blank.** They stay pending and can be filled in a later sitting. The report gives no result for a system until all of its rows are labeled.

## What stays closed while labeling

- The generated articles and every other file of `evals/.data/v2_compare/` except the sheet.
- `evals/datasets/v2_compare/labels.jsonl`: its ids name the system.
- Do not try to work out which system wrote a row.

No language model fills, suggests or corrects a label.

## After a sitting

Save the sheet as "CSV UTF-8" with `;` as separator, then:

```bash
uv run python -m evals.v2.compare import-labels
uv run python -m evals.v2.compare check-labels
```

`check-labels` lists rows to read again, by key: a quote labeled `S` that the code does not find in the reference text; rows of one article with the same type and value but different labels; `locuteur` in the note of a row that is not a quote labeled `C`. A listed row is not necessarily wrong. Read it again, keep or change the label in the sheet, and import again.

Do not run `export-labels` again between two sittings: a new export keeps only the rows that have no label, and a listed row must still be in the sheet to be corrected.
