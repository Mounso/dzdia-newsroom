# ADR-0020: A private chat holds a dossier of sources; `/ecris` writes one article from all of them

- **Status:** Accepted
- **Date:** 2026-11-16
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0019](0019-lightest-wiring-of-the-slice.md) (point 1: sources are stored; point 2: every link of a private message is read; point 3: the routing of a private chat; point 6: an answered refusal is a finished job; point 7: the replies of a private chat; point 9: no duration in the acknowledgments), [ADR-0018](0018-guarded-fetch-and-pasted-text.md) (point 12: a pasted text joins a dossier; point 13: a typed error no longer becomes `PermanentJobError`)
- **Related:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (point 8: the `drafts` table; point 10: the sources message), [ADR-0014](0014-quote-must-be-quoted-in-source.md) (the quote guard, unchanged), [ADR-0015](0015-dzdia-corpus-local-copy.md) (point 8: no Dzdia text is sent to a model), [ADR-0017](0017-v2-stays-the-production-path.md) (point 4: the plain-prompt control arm; point 5: a target length needs its own decision; point 7: the display of the sources); [corpus counters](../evals/dzdia-corpus.md#results); [data model](../architecture/data-model.md#retention)

## Context

The slice of ADR-0019 turns one web page or one pasted text into a short draft. It ran end to end on 2026-11-16 in a private chat: a draft took 2.4 to 3.1 seconds and one model call.

One source is not the material of an article. The journalists described their method as a social media post, then documentation, then an interview, then writing, and they asked for a long article with quotes and sources (ADR-0019, Context). ADR-0019 said that showing where a sentence comes from "waits for inputs with several sources".

The outlet's 573 published articles were counted again on 2026-11-16, with throwaway scripts that print counters only.

- **Skeleton:** an introduction of two paragraphs (median 611 characters), then four headings. The median length is 4,081 characters, and 5,826 over the 100 most recent posts.
- **Video:** 292 posts (51 %) embed a video of the outlet's own channel. In those posts 12.7 % of the text lies inside quotation marks: the interview gives the quotes, documentation gives the rest.
- **Sources:** the partner link apart, 358 posts carry no text link to an outside source. The corpus shows articles, not what they were written from.

Abdou's choices of 2026-11-16:

- `ds1`: a dossier of web links and pasted texts first. The interview video (`ds2`) and a search step (`ds3`) come later.
- Private chat first. The team group keeps the behavior of ADR-0019.
- `lg1`: the length of the article follows the material.
- `ev1`: the prompt is measured on six constructed dossiers, with blind labels.
- `mk3`: a clean article, then a second message that maps paragraphs to sources.
- Two questions were delegated to Claude: how a dossier is opened (`ou1`) and how long it lives (`du1`).
- `jp1`: the purge also empties the payload of old jobs.

The run also showed four defects that this ADR settles or names.

- The acknowledgment says "environ une minute", twenty times the measured duration.
- Three of six jobs ended `dead` at ERROR level, although each was an expected refusal that the journalist got an answer for.
- Wikipedia answers 403 to the worker container and 200 to the same client on Windows. Abdou chose `wk2`: no change, the journalist pastes the text.
- Rows of `jobs` are never deleted, and the payload of an `inbound_message` job holds the message text.

## Decision

1. **Scope** (`ds1`). In a private chat, the bot keeps one dossier of sources per chat. A source is a web page or a pasted text. Images, videos, files, search and an angle instruction are out of this batch. The team group is unchanged: ADR-0019, points 2, 3, 4 and 7, and the prompt `v2-article-2`.
2. **One mode in a private chat** (`ou1`; amends ADR-0019, point 3). A private message is routed by the first rule that applies.
   - It starts with a command (point 3): the command runs.
   - It holds at least one link: each link becomes a source (point 4).
   - Its text has at least 300 characters: it becomes a pasted-text source.
   - Anything else gets the help text, followed by the number of sources in the dossier.

   A private chat no longer gives an immediate draft: an article needs a source, then `/ecris`.
3. **Commands are read from the text**, not from Telegram's entities.
   - `/ecris` writes the article (point 12). `/nouveau` empties the dossier. `/start` gets the help text.
   - A command is the first word of the stripped text, compared without case, with an optional `@<bot name>` suffix removed. `/écris` is accepted as `/ecris`; Telegram's own command menu takes no accent.
   - A message that is exactly `ecris`, `écris` or `nouveau` is the same command. These bare words count only when they are the whole message.
   - The text after a command is ignored in this batch. An unknown command gets the help text.
4. **A source is fetched and stored when it arrives.**
   - Every link of the message is read, in order (amends ADR-0019, point 2, for a private chat). Each link is checked, fetched and extracted by the rules of ADR-0018, and succeeds or fails on its own.
   - A link that is already in the dossier, compared as the exact string, is not added again.
   - A pasted text is stripped and stored as it is. A long text that Telegram cuts into several messages arrives as several sources (amends ADR-0018, point 12).
   - The job sends one reply: a receipt with one line per link or text (its number, its host or "texte collé", its length, or its error), then the state of the dossier. No acknowledgment is sent before the fetch.
   - The 180-second deadline of ADR-0019, point 5, covers every fetch of the job.
5. **Caps, all provisional.** 6 sources per dossier. 30,000 characters per source, as today. 60,000 characters for the whole dossier: a source that exceeds the room left is cut to it by `cap_text` and flagged as cut. A source is refused, with a "dossier full" message, at 6 sources or when less than 300 characters of room is left.
6. **Two tables, migration `0002_dossiers.sql`** (amends ADR-0019, point 1).
   - `dossiers`: `id`, `channel`, `chat_id`, `created_at`, `last_activity_at`; unique on `(channel, chat_id)`.
   - `dossier_sources`: `id`, `dossier_id` (foreign key, `ON DELETE CASCADE`), `position` (1 to n, the number shown to the journalist), `kind` (`link` or `pasted_text`), `url` (the link as sent; null for a pasted text), `text` (the extracted text after the caps), `source_chars` (its length before the caps), `truncated`, `paywall_suspected`, `message_id`, `link_index`, `created_at`; unique on `(dossier_id, position)` and on `(dossier_id, message_id, link_index)`.
   - Only extracted text is stored, never HTML. No draft, article or evidence item is stored: evidence is rebuilt from the stored texts at each `/ecris`.
   - Adding a source is one transaction that locks the dossier row, checks the caps and inserts. The second unique key makes a replayed job add nothing.
7. **Lifetime** (`du1`). The dossier is kept after `/ecris`, so a source can be added and the article written again. `/nouveau` deletes its row, and its sources with it. A dossier expires 48 hours after its last activity; adding a source and `/ecris` are activity, the help text is not.
   - Every read deletes a dossier that has expired and treats it as empty, so the rule does not depend on when the purge runs.
   - A background task in the worker process deletes expired dossiers at start, then every hour. A failed run is logged and tried again at the next hour. The queue is not changed, and nothing is added to the host.
8. **The same task empties the payload of old jobs** (`jp1`). The payload of an `inbound_message` job that is `done` or `dead` and has not changed for 48 hours is replaced by `{}`. The row and its `dedup_key` stay, so a redelivered webhook is still refused. Without this, a pasted text would outlive its dossier in `jobs.payload`.
9. **Evidence ids run over the whole dossier.** Each source text is cut by `build_evidence` as today; the items are then numbered `E1…En` across the dossier, in source order. `Evidence` gains a `source` field, the position of its source. Ids are scoped to one `/ecris`.
   - The prompt groups the items in one `<source n="…">` element per source. Source text that contains `<source` or `</source` is neutralized, as `<evidence` is today.
   - The model never sees a URL or a host and never writes a source number: code derives the sources of a paragraph from the evidence ids it cites.
   - The quote guard of ADR-0014 is unchanged. A quote cites one evidence item, so one source.
10. **A new prompt, `v2-article-3`, with its own schema.** `v2-article-2` and its schema are not touched: the team group uses them, and the fingerprint of the prompt is pinned in `evals/v2/arms.py`.
    - **Several sources.** A fact found in several sources is stated once and cites an id from each. When two sources disagree, both versions are written, each with its ids, and the model does not choose.
    - **Headings.** A paragraph gains an optional `heading`. Like the title, a heading cites nothing and holds no quotation mark; the validator applies the title rule to it. When the budget of point 11 is at least 2,000 characters, the prompt asks for the outlet's skeleton: two introduction paragraphs without a heading, then 2 to 4 headed sections. Below, it asks for plain paragraphs.
    - **Length.** The prompt states the maximum of point 11, in words.
    - Everything else is kept from `v2-article-2`: the editorial rules, the quote rule, the notice on untrusted content, the validation and its single retry.
11. **The length follows the material** (`lg1`; the decision that ADR-0017, point 5, asked for).
    - Code computes a budget in characters: `min(6000, 0.8 × L + 0.4 × (T − L))`, where `T` is the length of all stored source text and `L` that of the longest source. The other sources count less because sources on one topic repeat each other.
    - The prompt gives the budget as a maximum in words (budget divided by 6, rounded to 50, at least 100) and says that a shorter article is right when the material runs out.
    - Length is never validated: no retry and no failure for an article that is too short or too long. A minimum enforced by code would invite the additions measured on the v1 prompt.
    - **Thin material:** when the budget is under 2,000 characters, the article starts with a warning line that says so and suggests adding sources.
    - The two coefficients, the 6,000 ceiling and the 2,000 threshold are guesses. They are tuned on tryout dossiers only and frozen with the prompt.

    | Dossier | Budget | Maximum stated |
    | --- | --- | --- |
    | 1 source of 1,800 characters | 1,440 | 250 words, thin |
    | 1 source of 4,000 | 3,200 | 550 words |
    | 3 sources of 3,000 | 4,800 | 800 words |
    | 4 sources of 5,000 | 6,000 (ceiling) | 1,000 words |
12. **`/ecris` sends two messages** (`mk3`; amends ADR-0019, point 7, for a private chat; settles ADR-0017, point 7). With an empty dossier it sends one message that says so, and calls no model. Otherwise it acknowledges on the first attempt, as in ADR-0019, point 4, then sends, as plain text with no `parse_mode`:
    - **The article:** the thin-material warning when it applies, the title, then each paragraph, with its heading on its own line. No marker.
    - **The sources:**
      - a numbered list of the sources: the host and, on the next line, the link as sent, or "Texte collé" with the first words of the text; a flag after a source that was cut or that seems reserved to subscribers;
      - one line per paragraph: its number, its first words (about 40 characters) and the numbers of the sources its evidence ids belong to;
      - the numbers of the sources that no paragraph cites;
      - the removed-quote count, with the wording of ADR-0014, point 6.
13. **French texts.** They stay constants in `worker/replies.py`, and a wording still needs no ADR. Three changes are fixed here.
    - No text states a duration. The three acknowledgments of the team group lose "(environ une minute)" (amends ADR-0019, point 9).
    - In a private chat, the "video or social link" and "site" messages suggest pasting the text into the dossier, not a draft.
    - New cases: the receipt, a link already in the dossier, a full dossier, the `/ecris` acknowledgment, an empty dossier, the `/nouveau` confirmation, the private help text, the thin-material warning, the sources message.
14. **An answered refusal is a finished job** (amends ADR-0019, point 6, and ADR-0018, point 13; applies to the team group too).
    - A typed error of the link or the site family, a full dossier and an empty dossier send their message, and the job ends `done`. The job's log line keeps the error type as its outcome, at WARNING level.
    - `GenerationFailedError`, the expired deadline, an invalid payload and a permanent channel error still send their message where one exists, then raise `PermanentJobError`: the job is `dead`, at ERROR level.
    - Any other error is left to the queue's retries, as today.

    A `dead` job then means that the bot failed, not that a site refused it.
15. **Logs.** The single line per job of ADR-0019, point 11, gains the routes `source`, `write` and `new`, and these fields: the number of sources and characters in the dossier, the outcome and host of each link of the message, the budget, the length of the article, its paragraphs and headings, and the sources cited and not cited. Never a text, or the path or query of a link.
16. **Modules.**
    - `tools/dossier.py`: adding a link, adding a text, emptying, and writing the article from a dossier. It holds the caps, the evidence numbering of point 9 and the budget of point 11, and reaches the database through a repository interface. It knows nothing of Telegram.
    - `tools/article.py`, or a module next to it: the prompt `v2-article-3`, its schema and its generation, on the validator that exists.
    - `db/dossiers.py`: the repository of the two tables and the two purges.
    - `worker/replies.py`: the texts, the private routing and the rendering of the two messages. `worker/handlers.py` stays a thin adapter. `worker/__main__.py` starts the purge task.
    - Tests use no real network and no real model. The repository and the purges are tested on Postgres under `tests/db/`.
17. **Measurement** (`ev1`). `v2-article-3` is measured on six constructed dossiers of 3 or 4 public press pages each, frozen under `evals/.data/`, with no Dzdia content, every element labeled blind by Abdou, and a plain-prompt arm on the same dossiers (ADR-0017, point 4).
    - The protocol gets its own ADR before any run: the arms, the primary result, what will be written for each outcome, and the tryout dossiers, which are not among the six.
    - Each real fetch and each real model run needs its own go-ahead.
    - No rate is stated for `v2-article-3` before that report.
18. **Not built here.**
    - `wk1`, Wikipedia's official API: open. It is decided, in its own ADR, once a first fetch from the production VM shows whether Wikipedia refuses that host too.
    - The interview video (`ds2`), search (`ds3`), a dossier in the team group, removing one source, listing the dossier with a command, an angle instruction, stored drafts, and the worker health check (A-4).
    - Two findings of the run that need no decision are left as plain fixes: the traceback that `set-webhook` prints for a rejected URL, and the "AFC is enabled" line that google-genai logs on every call.

## Alternatives considered

- **Keeping the immediate draft, with a command that opens a dossier** (`ou2`): a single link still gets its draft in one message. But the same message would do two different things depending on a mode the journalist cannot see.
- **Deleting the dossier once the article is sent** (`du2`): nothing is kept. But adding one source and writing again, the most likely second step, would mean sending every source again.
- **A fixed target of about 4,000 characters** (`lg2`): close to the outlet's median. But a thin dossier would be padded to reach it; ADR-0017 rejected a target for that reason.
- **Length bounds enforced by the validator**: the article would always land in its range. But a minimum makes the model add text, and a retry "write more" has no evidence to draw on.
- **Automatic counters and a reading by eye, with no labels** (`ev2`): a few hours saved. But it breaks the rule that a rate comes with labels and an interval. **Waiting for real dossiers from the outlet** (`ev3`): the only measure close to real use, but the outlet cannot provide the sources of its published articles yet.
- **A list of sources at the end of the article, with no link to the paragraphs** (`mk1`): one message. But it does not show where a paragraph comes from, the property ADR-0017 kept the pipeline for. **`[n]` markers at the end of each paragraph** (`mk2`): the mapping sits in the text. But the journalist must remove the markers before publishing.
- **Fetching the links at `/ecris`**: no page text is stored. But a dead link or a refusing site would be found only when the journalist asks for the article, and all errors would arrive together.
- **Only the first link of a message, as in ADR-0019**: one rule in the group and in private. But sending several links in one message is the natural way to give a dossier.
- **Evidence ids prefixed per source** (`S2-E5`): the source is readable in the id. But the model would write a longer id on every sentence, with one more way to get it wrong; code already knows the source of each item.
- **One summary per source, then the article from the summaries**: a smaller final prompt. But two passes cost twice the calls, quotes can no longer be checked against the source, and ADR-0009 rejected two-pass writing.
- **A job kind per command, or a chain of jobs per link**: retries per step. But more job kinds and payloads, for fetches that already retry once inside their own deadline.
- **The purge as a cron on the VM, or as a recurring job in the queue**: no task inside the worker. But the first adds host configuration that the dev stack would not have, and the second needs scheduled jobs, which the queue does not have.
- **A purge only when a dossier is read**: no background task at all. But the text of a journalist who never writes again would stay for good.
- **Leaving job payloads as they are** (`jp2`): nothing to add to the queue's table, and retention (R4) keeps its own ADR. But the 48-hour rule would hold for the dossier tables only, while every pasted text stays in `jobs.payload`.
- **Reading Telegram's command entities**: the platform's own parsing. But their offsets count UTF-16 code units, and an accented `/écris` is not an entity.

## Consequences

- **A single link now takes two messages in a private chat**: the link, then `/ecris`. The team group still answers a link with a draft, so the two places behave differently until the group is taken up again.
- **Two prompts run in production**: `v2-article-2` in the group, `v2-article-3` in private.
- **The database holds third-party press text and journalists' pasted texts for up to 48 hours after the last activity.** With `jp1`, a dead job can no longer be replayed by hand after 48 hours.
- **The order of a dossier rests on a single worker.** Jobs of one chat run in arrival order. A source job that fails on an unknown error and is retried can land after the `/ecris` that followed it; the sources message shows which sources the article used. A second worker replica would need an order per chat.
- **A replayed `/ecris` job sends a second article**, as a replayed job did in ADR-0019. A replayed source job adds nothing but sends its receipt again.
- **The length rule is unmeasured.** The coefficients come from one measured ratio (a draft of about 0.8 times a single short source) and a guess on overlap.
- **Headings cite nothing**, like the title. A heading can state something that no source says; the labels of the eval read headings as part of the article.
- **The sources map shows which sources a paragraph cites, not that each citation is right.** A weak citation is still possible (ADR-0009, third consequence).
- **A long prompt is unmeasured.** A full dossier is 60,000 characters of source text; every earlier run stayed under 30,000.
- **Quotes still come only from words that a press page puts inside quotation marks.** A dossier does not bring the interview quotes of the outlet's articles; that is `ds2`, with its own quote rule (ADR-0014, second consequence).
- **A source cannot be removed alone.** A wrong link means `/nouveau` and sending the others again.
- **A pasted text cut by Telegram becomes several sources**, and a quote that spans the cut is lost.
- **Wikipedia pages must be pasted**, at least from the development machine.
- **The worker needs migration `0002` applied before it starts.**
- To update when this ADR is accepted: the status lines of ADR-0018 and ADR-0019, and the index. With the code: `docs/architecture/data-model.md` (the two tables, retention), `overview.md`, `job-queue.md` (what `dead` means), `security.md` (stored text, the purge), `docs/operations/runbook.md` and `docs/roadmap.md`.
- Next: the code and its tests; the eval protocol ADR; then, each on its own go-ahead, the tryout dossiers, the freeze of `v2-article-3` and the six eval dossiers.
