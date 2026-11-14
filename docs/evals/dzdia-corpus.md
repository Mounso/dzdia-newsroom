# Dzdia corpus

A local copy of the articles published on dzdia.com, kept as a style reference ([ADR-0015](../adr/0015-dzdia-corpus-local-copy.md)). The v2 eval measures faithfulness on general news from two other outlets; this corpus shows what the newsroom itself publishes, so that the two can be compared with figures.

The corpus is not a test set: the sources of an article are not recorded, so nothing here measures faithfulness. No article text is committed, and no article is sent to a model by these steps.

## Conditions of use

- The publisher agreed to this local copy. The site's `robots.txt` sets `ai-train=no`: the corpus is never used to train or fine-tune a model.
- Putting article text in a prompt or in a search index is a separate decision, with its own ADR (ADR-0015, point 8).
- Only the public REST API is read, with no credential: the copy holds what any reader can see.

## Commands

```bash
uv run python -m evals.dzdia_corpus fetch
uv run python -m evals.dzdia_corpus fetch --full
uv run python -m evals.dzdia_corpus stats
uv run python -m evals.dzdia_corpus stats --following <path to a CSV with a username column>
```

- `fetch` lists the published posts, 100 per request, and stores each record as returned. The first run lists every page. A later run asks only for posts modified after the latest stored modification date, minus one day; `--full` lists every page again. A post file is rewritten only when the post's modification date changed.
- Requests are spaced by at least 2 seconds, time out after 30 seconds, and are retried at most 3 times on transport errors, 429 and 5xx. Each request is logged with its status and latency. A run that fails leaves the manifest and the fetch log as they were; the next run reconciles with the files already written.
- `stats` prints counters only: no title, no article text, no account name. `--following` reads a local list of followed accounts and prints counts against it; the list stays outside the repository.

The one-day margin on the incremental listing is a safety choice: how the API compares the date with the site's local time is not verified, and a wider listing only costs a few posts counted as unchanged.

## Files

| File | In Git | Content |
| --- | --- | --- |
| `evals/.data/dzdia_corpus/posts/<id>.json` | No | The API record of one post: rendered HTML, title, dates, category and tag ids, author id |
| `evals/.data/dzdia_corpus/categories.json` | No | Category ids and names |
| `evals/datasets/dzdia_corpus/manifest.jsonl` | Yes | One line per post: id, URL, publication and modification dates, category names, SHA-256 and length of the rendered HTML |
| `evals/datasets/dzdia_corpus/fetches.jsonl` | Yes | One line per run: date, mode, requests, posts new, changed and unchanged |

The manifest holds no title, no text and no author.

## What `stats` counts

The definitions are in the docstring of `evals/dzdia_corpus/stats.py`. In short:

- **Body text**: the rendered HTML without scripts, styles and the body of social media embeds, whitespace collapsed. Characters, words, paragraphs, headings (`h2` to `h4`) and italics are counted on it.
- **Quoted passages**: spans between `«` and `»`, by length band (under 40 characters, 40 to 59, 60 or more), and the share of body characters inside them. Short spans are mostly titles of works and expressions; the 60-or-more band is a proxy for reported speech, not a measure of it.
- **Titles** that contain a quotation mark.
- **Embeds**: posts with an iframe, by host, and posts with an Instagram, TikTok or X embed.
- **Instagram accounts cited**: from links and embeds, the outlet's own account excluded. An account cited in an article is the account of its subject; it does not prove where the idea for the article came from.

## Results

Not run yet. A first look on 2026-11-14, made with throwaway scripts on the 200 most recent articles, is quoted in ADR-0014 and ADR-0015; the figures of the committed step will replace it here.
