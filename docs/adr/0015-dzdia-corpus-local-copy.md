# ADR-0015: The published Dzdia articles are copied locally by a committed step, as a style reference only

- **Status:** Accepted
- **Date:** 2026-11-14
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0001](0001-baseline-hallucination-protocol.md) (the eval sample this corpus is compared to), [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (the slice whose output is compared to it), [ADR-0014](0014-quote-must-be-quoted-in-source.md) (its scope limit rests on a first look at these articles)

## Context

The v2 eval measures faithfulness on 16 general-news articles from two outlets (TSA, Algérie 360). The newsroom this system is built for publishes something else: long cultural articles, often built on its own interviews and on social media posts. A first look at the 200 most recent articles of dzdia.com on 2026-11-14 gave a median length of 5,193 characters, against 1,305.5 for the v2 articles of the comparison; 138 of 200 carry at least one quoted passage of 60 characters or more; 65 of 200 titles contain quotation marks, which the v2 validator refuses in a title (ADR-0012).

Those figures came from throwaway scripts and cannot be reproduced. The decisions that follow (what stage 2 keeps of the architecture, the scope of the quote guard, later a style target and a search index) need the same articles to be read again, by code.

Constraints:

- The site has 573 published articles, served by the public WordPress REST API (`/wp-json/wp/v2/posts`), 100 per request.
- Its `robots.txt` sets `Content-Signal: search=yes, ai-train=no, use=reference` for every agent, sets no `ai-input` signal, and disallows a list of AI crawlers and AI user agents.
- The publisher agreed to a local copy of its articles for this project (confirmed by the author on 2026-11-14).
- Copyrighted article text stays in `evals/.data/` (project invariant), and the repository is public.

## Decision

1. **A committed command.** The package `evals/dzdia_corpus/`, run as `python -m evals.dzdia_corpus <step>`, with the steps `fetch` and `stats`.
2. **Access: the public REST API, with the publisher's agreement.** No credential is used: the copy holds what any reader can see. Requests carry the User-Agent of the baseline's fetches, are spaced by at least 2 seconds, have a 30-second timeout and at most 3 retries with backoff on transport errors, 429 and 5xx, and each is logged with its status and latency.
3. **Scope: every published post.** The first run lists all pages in ascending id order. A later run asks only for posts modified since the latest `modified_gmt` already stored (`modified_after`), so it downloads new and edited articles only; `--full` lists everything again. Pages, media files and comments are not fetched. Category names are fetched once per run; tags are kept as ids.
4. **What is stored locally** (`evals/.data/dzdia_corpus/`, not in Git): one file per post, `posts/<id>.json`, holding the API record as returned (rendered HTML, title, dates, category and tag ids, author id), and `categories.json`. A file is rewritten only when the post's `modified_gmt` changed. A post that the API no longer lists keeps its file.
5. **What is committed** (`evals/datasets/dzdia_corpus/`):
   - `manifest.jsonl`, one line per post: id, URL, publication and modification dates, category names, the SHA-256 and the length of the rendered HTML. No title, no text, no author.
   - `fetches.jsonl`, one line per run: date, mode (first, incremental, full), requests made, posts new, changed and unchanged.
6. **The `stats` step** reads the local files and prints counters only: length in characters and words, headings, paragraphs, quoted passages by length band, the share of characters inside quotation marks, titles with quotation marks, italics, embeds by provider, and the social media accounts cited. Its figures go in `docs/evals/dzdia-corpus.md`. It prints no article text.
7. **Use: a style reference.** The corpus is read by `stats` and by people. This ADR puts no article, in whole or in part, into a prompt, an index or a committed file.
8. **Limits on any later use.**
   - The corpus is never used to train or fine-tune a model (`ai-train=no`).
   - Sending article text to a model, as examples in a prompt or as retrieved passages, is a separate decision. It needs its own ADR, a new prompt version where a prompt changes, and a check that the API tier in use does not reuse inputs to improve the provider's models.

## Alternatives considered

- **A throwaway script**: ten minutes instead of an hour, but the figures that later decisions rest on could not be rebuilt, which is the defect of the first look.
- **Only the 200 most recent articles**: enough for medians, but a search index or a duplicate-topic check will need the whole archive, and the full listing costs six requests.
- **Clean text only**: lighter, but headings, italics and embeds, which the first look counted, are in the HTML.
- **Titles in the committed manifest**: easier to read, but a title is a piece of the article, and the URL already identifies it.
- **An export from the WordPress admin, or the authenticated API**: gives the raw blocks, drafts and revisions, but it needs a credential for content the public API already serves, and each update needs a new export. Credential work belongs with the WordPress write tool and its own ADR.
- **Dzdia articles as examples in the v2 prompt now**: aims at the real goal, articles that read like the outlet's. But an example without its source teaches style and not grounding; it pulls the model toward 5,000 characters that one source page cannot support, which is the defect measured with the v1 prompt; no style metric exists yet to show a gain; it would change the prompt at the same time as ADR-0014; and a prompt committed in a public repository cannot contain the outlet's text.
- **A search index now**: the corpus is its future input, but retrieval is outside the stage 2 slice.
- **No copy, keep the counters of the first look**: nothing to build, but nothing reproducible either.

## Consequences

- A second eval package appears beside `baseline_v1` and `v2`; it shares their conventions (local data in `evals/.data/`, committed metadata in `evals/datasets/`, counters only on screen).
- The manifest publishes which articles were copied and when, not their content. Anyone can check a hash against the public page.
- The local copy must be covered by the same care as the snapshots: it is not committed, not put in an image, and not sent to an external service by this step.
- The rendered HTML carries the theme's and plugins' markup (embeds, affiliate links); `stats` must count on the text and the structure, not on that markup.
- The corpus shows what the outlet publishes, not what its sources looked like: it cannot serve as a faithfulness test set, since the source of each article is not recorded.
- Next: the code and its tests, the first run on the author's go-ahead, then `docs/evals/dzdia-corpus.md` with the figures, which replaces the throwaway figures quoted in ADR-0014.
