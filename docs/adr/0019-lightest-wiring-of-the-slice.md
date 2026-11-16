# ADR-0019: The slice is wired in its lightest form: one handler, no stored draft, one reply with a source line

- **Status:** Accepted
- **Date:** 2026-11-15
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (point 8: no `drafts` table; point 9: no hook in the queue; point 10: the acknowledgment, the group rule and the rendering), [ADR-0011](0011-failed-quote-is-removed.md) (point 3: where the count of removed quotes is shown), [ADR-0018](0018-guarded-fetch-and-pasted-text.md) (point 12: how pasted text is named as a source)
- **Amended by:** [ADR-0020](0020-source-dossier-in-private-chat.md) (point 1: sources are stored; points 2, 3 and 7: the links, the routing and the replies of a private chat; point 6: an answered refusal is a finished job; point 9: no duration in the acknowledgments)
- **Related:** [ADR-0005](0005-telegram-ingress.md) (allowlist, settings split per process), [ADR-0014](0014-quote-must-be-quoted-in-source.md) (point 6: the wording of the removed-quote count), [ADR-0017](0017-v2-stays-the-production-path.md) (point 7: the display of the sources is decided here); [independent review](../reviews/2026-11-13-independent-review.md), findings V-7, V-8, V-13, V-14, A-4; [corpus counters](../evals/dzdia-corpus.md#results)

## Context

Fetching, extraction, evidence and generation exist and are tested without the network. The worker still answers "Bien reçu." to every message. This ADR wires them. ADR-0018, point 15, left four things to this step: the French texts, how a link is found in a message, how warnings are rendered, and the `drafts` table. ADR-0017, point 7, left the display of the sources.

ADR-0009 planned a `drafts` table, a hook in the queue for dead jobs, and two messages with `[n]` markers. The independent review questioned parts of that plan.

- **V-7:** in a group, Telegram's privacy mode delivers only commands, mentions and replies to a bot. With that mode off, the bot would answer every message of every allowlisted journalist. Not tested.
- **V-8:** the acknowledgment is sent by the worker, so it waits behind a running job, and it is sent again on every attempt.
- **V-13:** the `drafts` table could wait: a replay is rare and a second call on a lite model costs a fraction of a cent.
- **V-14:** a second worker replica would remove the wait of V-8.
- **A-4:** the worker has no health check.

The journalists answered the discovery questions on 2026-11-15, as reported by Abdou.

- An article starts from a social media post, then documentation, then an interview with the person, then writing.
- They want a long article with quotes and its sources, not a short draft to complete.

The outlet's own articles agree on length and quotes: the median is 4,081 characters and 353 of 573 posts carry a quoted passage of 60 characters or more. A throwaway count of interview wording ("à Dzdia" after a verb of speech, questions in bold) found it in 204 of 573 posts; that figure is not produced by a committed step.

So one web page is not the material of an article. The slice serves the documentation step. Abdou chose to finish it in its lightest form by 2026-11-22, then to move to the morning briefing and to the interview as an input. Each part of the ADR-0009 plan that only pays off for a finished product is dropped here.

## Decision

1. **One handler, nothing stored** (amends ADR-0009, point 8). The `inbound_message` handler runs the whole chain in one job: read the message, acknowledge, fetch, extract, build evidence, generate, send. There is no `drafts` table and no migration. A job that runs again does everything again; a second draft on a rare replay is accepted.
2. **Finding a link.** A link is a match of `https?://\S+` in the message text, scheme case-insensitive, with trailing punctuation removed (`. , ; : ! ? ) ] } » " ' …`). A closing parenthesis at the end is kept when the link holds at least as many opening parentheses as closing ones, so a link such as `…/Babylone_(groupe)` stays whole. Only the first link is processed. Telegram's own entities are not read.
3. **What the bot does with a message.**
   - **Private chat.** A link starts the link path. With no link, a text of at least 300 characters starts the pasted-text path (ADR-0018, point 12). Anything else gets the help text.
   - **Team group** (amends ADR-0009, point 10). A link starts the link path. Any other message gets no answer, and its job completes. Pasted text and the help text exist only in a private chat.
   - The allowlist of ADR-0005 is unchanged and stays in the API.
4. **The acknowledgment** (amends ADR-0009, point 10). The worker sends it as a reply to the message, before the fetch, and only on the first attempt of the job. A link refused before any network call (the URL rule or the host list of ADR-0018) gets its error message and no acknowledgment: `tools/fetch_page.py` exposes that check as a public function, and `fetch_html` keeps calling it.
5. **A deadline of 180 seconds** covers the fetch and the generation of one job, not the sends. When it expires the journalist gets the "inside" message and the job is not retried.
6. **Errors** (amends ADR-0009, point 9; applies ADR-0018, point 13).
   - A typed error of the link or the site family sends its message as a reply, then becomes `PermanentJobError`.
   - `GenerationFailedError` and the deadline send the "inside" message, then become `PermanentJobError`.
   - Any other error is left to the queue's retries. On the last attempt the handler sends the "inside" message, then lets the error through. The queue is not changed.
   - Error messages are sent in the team group too: the journalist sent a link there.
   - A failure while sending is translated as today: a permanent channel error ends the job, a rate limit sets the wait, anything else follows the queue's backoff.
7. **One reply, with one source line** (amends ADR-0009, point 10, and ADR-0018, point 12; settles ADR-0017, point 7). The reply is plain text with no `parse_mode`, in this order:
   - the warnings that apply, one per line (point 9);
   - the article as `render_text` gives it, with no `[n]` marker;
   - `Source : <link>`, with the link as the journalist sent it, or `Source : texte collé`;
   - when quotes were removed, their count, with the wording of ADR-0014, point 6: "1 citation retirée : non confirmée par la source.", "2 citations retirées : non confirmées par la source." Only its place changes (amends ADR-0011, point 3).

   There is no second "Sources" message and no excerpt.
8. **Long replies are split by the Telegram adapter.** `send_text` splits a text longer than 4,096 UTF-16 code units, at the last blank line that fits, else the last line break, else the last space. Only the first part is a reply to the journalist's message. The worker never sees the limit.
9. **French texts.** They live as constants in `worker/replies.py`. Changing a wording needs no ADR.

   | Case | Text |
   | --- | --- |
   | Acknowledgment, link | Lien reçu, je prépare un brouillon (environ une minute). |
   | Acknowledgment, several links | Lien reçu, je prépare un brouillon (environ une minute). Je ne traite que le premier lien du message. |
   | Acknowledgment, pasted text | Texte reçu, je prépare un brouillon (environ une minute). |
   | Help | Envoyez-moi le lien d'un article, ou collez son texte (au moins 300 caractères) : je vous renvoie un brouillon avec sa source. |
   | Link family | Je n'ai pas pu ouvrir ce lien. Vérifiez qu'il est complet et qu'il s'ouvre dans un navigateur, puis renvoyez-le. |
   | Video or social link | Je ne lis pas encore les vidéos ni les publications des réseaux sociaux. Collez-moi le texte ou la légende (au moins 300 caractères) et j'en fais un brouillon. |
   | Site family | Le site ne m'a pas laissé lire cette page. Collez-moi le texte de l'article dans un message et j'en fais un brouillon. |
   | Inside | Je n'ai pas réussi à écrire ce brouillon. Le problème vient de mon côté, pas de votre envoi. Réessayez dans quelques minutes. |
   | Warning, paywall | ⚠ Cette page semble réservée aux abonnés : le brouillon ne couvre que la partie visible. |
   | Warning, pasted text of 4,000 characters or more | ⚠ Ce texte est peut-être coupé par Telegram : le brouillon ne couvre que ce message. |
   | Warning, source cut at 30,000 characters | ⚠ Le texte est très long : le brouillon ne couvre que le début. |

   In the team group the "video or social link" and "site" messages keep their suggestion to paste the text; a text pasted in the group gets no answer, so the journalist pastes it in a private chat.
10. **The worker gets the model settings.** It loads `LLMSettings` and refuses to start without the Gemini key. It builds one generator for the process, with `GENERATION_MODEL`, the way the eval's v2 arm builds it, so production runs what was measured. It builds one guarded HTTP client with `httpx.Timeout(PHASE_TIMEOUT_S)`. `docker-compose.yml` passes `GEMINI_API_KEY` and `GENERATION_MODEL` to the worker only; the API still holds neither the bot token nor the model key.
11. **Logs replace the table.** Each job logs one line: the path (link, pasted text, help, silent), the outcome (ok, or the error type), the host of the link, the length of the source text, the number of evidence items, model calls and removed quotes, the warnings raised, the model, the prompt version and the duration. Never the message text, the path or query of the link, or the article.
12. **Modules.**
    - `tools/draft.py`: the composition for a link (fetch, extract, cap, evidence, generate) and for a pasted text (cap, evidence, generate), returning the validated article, its evidence and the warnings.
    - `worker/replies.py`: the texts, the link search, the routing rule of point 3, and the rendering of point 7.
    - `worker/handlers.py`: the handler, a thin adapter over the two.
    - `channels/telegram.py`: the split of point 8.
    - Tests use no real network and no real model: a fake generator, and an HTTP client on a mock transport.
13. **Not built here.** A worker health check (A-4) waits for the deployment on the VM. A second worker replica (V-14), the `drafts` table, the `[n]` markers and the sources message can each come back with their own decision.

## Alternatives considered

- **Stopping the slice now and starting the briefing**: it goes straight to what the journalists value most. But the pipeline would never run end to end, and the briefing would redo part of this wiring (worker settings, long messages, visible errors).
- **`[n]` markers and a "Sources" message with excerpts** (ADR-0009, point 10): the journalist can check sentence by sentence. But with one source page the sources message is often longer than the draft, it is the largest piece of rendering code, and nobody has said they would read it.
- **Private chats only**: no noise is possible. But the team group, already allowed by the webhook, would be useless.
- **The same behavior in the group and in private**: one rule. But any long message between colleagues would become a draft, and every short one would get the help text.
- **No acknowledgment**: one message less to send. But the journalist would get nothing during the generation, and nothing at all for a long time when a job is retried.
- **The acknowledgment sent by the API**: immediate. But the API would need the bot token, which ADR-0005 keeps out of it.
- **Two worker replicas**: removes the wait behind a running job with no new code. But it doubles a process on a small VM for a slice with one or two users; the deadline of point 5 bounds the wait instead.
- **A hook in the queue for dead jobs** (ADR-0009, point 9): it also covers a job that dies because its worker crashed. But it changes the queue and its Postgres tests for a rare case.
- **Keeping the `drafts` table**: no second model call and no second draft on a replay, and a record of what the guard did. But it needs a migration, a repository and their Postgres tests for a case that costs a fraction of a cent.
- **Reading Telegram's link entities**: it also finds a link written without `https://`. But their offsets count UTF-16 code units, and the links journalists share from an app carry their scheme.
- **Splitting long replies in the worker**: one function to test. But the worker would know a limit that belongs to one platform.

## Consequences

- **The evidence ids are validated and never shown.** Of the two properties ADR-0017 kept v2 for, a journalist sees only the checked quotes in this slice. Showing where a sentence comes from waits for inputs with several sources, where it starts to matter.
- **Nothing is stored.** A draft cannot be sent again or inspected later, and how often the validator retries or removes a quote is known from the logs only.
- **A second journalist can wait up to about three minutes for the acknowledgment**, behind a job that reaches its deadline.
- **A replayed job costs a second model call and can send a second draft.** A reply in several parts that fails after its first part is sent again from the start.
- **A worker that crashes during the last attempt of a job leaves the journalist with no message.**
- **Most of what journalists send gets the "video or social link" answer** (ADR-0018). The slice helps with documentation from press pages, not with writing from an interview.
- **A link written without its scheme is not seen.** It gets the help text, or is taken as pasted text when the message is long.
- **The group rule rests on an untested reading of Telegram's privacy mode.** With that mode on, a plain link sent to the group does not reach the bot; turning it off is a setting of the bot, outside this repository.
- **"About one minute" in the acknowledgment is not measured**, and neither is the 180-second deadline. The first end-to-end run gives the real durations.
- **The worker now holds the model key.** To update when this ADR is accepted: the status lines of ADR-0009, ADR-0011 and ADR-0018, the index, `docs/architecture/security.md` and `overview.md`, `docs/roadmap.md`, and the operations page for the two new worker variables.
- Next: the code and its tests; then, each on its own go-ahead, a first real page fetch and an end-to-end run with the development bot. When stage 2 ends, the order between the briefing, the interview as an input and the VM deployment is decided.
