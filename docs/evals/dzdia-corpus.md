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

### Fetch (2026-11-14)

| Run | Mode | Requests | Posts listed | New | Changed | Unchanged | Posts in the manifest |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | first | 7 | 573 | 573 | 0 | 0 | 573 |
| 2, right after | incremental | 2 | 1 | 0 | 0 | 1 | 573 |

No request was retried. The second run checks the incremental listing against the real site: with the one-day margin it lists one post, already stored and unchanged. It does not show that a modified post is picked up; no post changed between the two runs.

### Counters (2026-11-14, 573 posts published from 2025-04-02 to 2026-11-13)

| Body text | Minimum | First quartile | Median | Third quartile | 90th percentile | Maximum |
| --- | --- | --- | --- | --- | --- | --- |
| Characters | 1,073 | 2,989 | 4,081 | 5,191 | 6,600 | 12,166 |
| Words | 166 | 482 | 646 | 829 | 1,039.2 | 1,908 |
| Paragraphs | 3 | 9 | 12 | 17 | 22 | 43 |
| Headings (`h2` to `h4`) | 0 | 3 | 4 | 5 | 6 | 21 |

| Counter | Value |
| --- | --- |
| Posts with italics | 466 of 573 |
| `« »` spans under 40 characters | 879 |
| `« »` spans of 40 to 59 characters | 180 |
| `« »` spans of 60 characters or more | 1,364 |
| Posts with a `« »` span of 60 or more | 353 of 573 (three or more: 211; median per post: 1) |
| Share of body characters inside `« »` spans | 10.2 % |
| `“ ”` spans | 392, in 129 posts |
| Titles with quotation marks | 128 of 573 |
| Posts with a YouTube iframe | 364 |
| Posts with an Instagram embed | 160 |
| Posts with a TikTok embed | 6 |
| Posts citing an Instagram account | 163, for 174 distinct accounts |
| Cited accounts that are in the followed list (762 accounts) | 99 of 174 |
| Posts citing at least one followed account | 115 of 163 |

Largest categories (a post can have several): Culture 490, Musique 152, Livres 94, Actualités 83, Société 71, Films 68.

What the counters say, next to the v2 eval:

- **The articles are about three times longer than a v2 article.** The median is 4,081 characters, against 1,305.5 for the v2 arm of the [comparison](v2-comparison.md) and 3,504.5 for `page_text`. The 200 most recent posts are longer still (median 5,193 in the first look quoted in ADR-0014).
- **Quoted speech is the norm.** 353 of 573 posts carry at least one quoted passage of 60 characters or more, and a tenth of the body text is inside quotation marks. On the 22 frozen pages of the eval, 11 have no quotation mark at all.
- **A fifth of the titles contain quotation marks** (128 of 573), which the v2 validator forbids in a title (ADR-0012).
- **Social media is a visible source.** 160 posts embed an Instagram post, and 115 of the 163 posts that cite an account cite one the outlet follows. A cited account is the subject's account: this does not show where the idea came from.

Limits: these are counts of markup and punctuation, not of content. A `« »` span of 60 characters or more is a proxy for reported speech, and a title of a work can be longer than that. The counters describe one outlet at one date, and the followed list is a single export.
