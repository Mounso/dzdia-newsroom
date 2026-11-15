# ADR-0017: The v2 pipeline stays the production path of the slice, for code-checked quotes and traceable sources, not for a lower rate

- **Status:** Accepted
- **Date:** 2026-11-15
- **Deciders:** Abdou (author), with Claude as reviewer
- **Related:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (the pipeline), [ADR-0010](0010-v2-comparison-protocol.md) (point 11: what may be concluded), [ADR-0014](0014-quote-must-be-quoted-in-source.md) (the quote guard and its scope), [ADR-0015](0015-dzdia-corpus-local-copy.md) (the outlet's corpus); [comparison results](../evals/v2-comparison.md#labels-and-report-2026-11-14), [corpus counters](../evals/dzdia-corpus.md#results)

## Context

The comparison of 2026-11-14 measured the v2 pipeline against a plain prompt that carries the same editorial instructions, on 16 press articles ([results](../evals/v2-comparison.md#labels-and-report-2026-11-14)):

| Arm | Unsupported elements | Elements | Median length | Quotes |
| --- | --- | --- | --- | --- |
| `page_text` (v1 prompt, given the page text) | 12.9 % [8.5–18.1 %] (33/256) | 256 | 3,504.5 | 12, of which 8 unsupported |
| `plain_prompt` (new prompt, no architecture) | 0.0 % (0/227) | 227 | 1,455.5 | 0 |
| `v2` (evidence ids, structured output, validator) | 2.6 % [0.0–6.3 %] (5/194) | 194 | 1,305.5 | 16 kept, none reworded, 2 misattributed |

- **On the unsupported rate, the architecture brings nothing.** ADR-0010, point 11, fixed the reading in advance: the gain over `page_text` comes from the prompt. The question left open is whether v2 should still be the pipeline that ships.
- **The rate does not see what the plain prompt leaves out.** It wrote no direct quote in 16 articles, although its prompt neither asks for quotes nor forbids them. v2 is the only arm that writes quotes whose wording is checked by code.
- **4 of the 5 unsupported elements of v2 came from 2 false quotes.** The guard of ADR-0014 now refuses them. Replayed on the recorded drafts, it keeps 11 of the 16 quotes, in 6 articles of 16. No unsupported rate exists for the guarded prompt, `v2-article-2`.
- **The outlet's own articles are not like the eval sample** ([counters](../evals/dzdia-corpus.md#results)): 353 of 573 posts carry a quoted passage of 60 characters or more, the median length is 4,081 characters, and 160 posts embed an Instagram post.

Abdou's answers of 2026-11-15, as the person who knows the newsroom. No journalist has been asked yet; the discovery session belongs to stage 3.

- Whether a journalist would open the sources message to check a draft is unknown, and probably depends on the subject.
- A draft should carry direct quotes, since most of the outlet's articles do.
- The team will send a bit of everything, but most topics come from social media feeds.
- A short draft is an acceptable starting point; a somewhat longer one may fit better later.
- The goal is to save the journalists time.

## Decision

1. **The v2 pipeline is the production path of the slice**: ADR-0009 as amended by ADR-0011, ADR-0012 and ADR-0014, with the frozen prompt `v2-article-2`. The rest of stage 2 (fetching, worker and Telegram wiring) is built around it.
2. **It is kept for two properties, not for the rate.**
   - Direct quotes whose wording is checked by code, and that the source itself puts inside quotation marks.
   - Every sentence carries the ids of the evidence it rests on, so code can show the journalist where it comes from.
3. **What may be written about this choice.**
   - Allowed: v2 lowers the unsupported rate by 10.3 points [5.8; 15.3] against the v1 prompt given the same page text; a plain prompt with the same instructions does as well; v2 is kept for code-checked quotes and traceable sources.
   - Not allowed: any sentence that credits evidence ids, structured output or the validator with a lower unsupported rate; "v2 is more faithful than a plain prompt"; any rate for `v2-article-2` (ADR-0014, point 8).
   - The usefulness of the sources message is stated as untested until journalists have used it.
4. **The plain prompt stays as the control arm.** `v1-plain-1` stays frozen and pinned in `evals/v2/arms.py`, and production never calls it. Any later labeled comparison of the article pipeline runs a plain-prompt arm with the same editorial instructions on the same pages.
5. **The prompt is not changed now.** `v2-article-2` has no length instruction and gets none. A target length is a prompt change: it needs a new version, a tryout and its own decision.
6. **The choice is revisited after the stage 3 discovery session.** The journalists are asked whether they read the sources message, whether they want direct quotes in a draft, and what they send.
   - They want neither quotes nor sources: a new ADR ships the plain prompt.
   - They want quotes but do not use the sources: a new ADR weighs a pipeline with typed quotes and no evidence ids on sentences, with its own prompt version and tryout.
   - Otherwise this decision stands.
7. **Three questions are left to their own steps.**
   - How the sources are displayed (the `[n]` markers, the second message): at the Telegram wiring step. ADR-0009, point 10, stands until then.
   - Which inputs the slice accepts besides a web link (pasted text, a social media post): at the fetching step.
   - The quote rule for a first-person source: when that input is added (ADR-0014, second consequence).

## Alternatives considered

- **Shipping the plain prompt, with v2 kept as an eval arm**: the simplest pipeline, and no unsupported element in 227. But it writes no quote. Asking it for quotes would print words that nothing checks, in the category where the v1 prompt failed most (8 of its 12 quotes unsupported).
- **A plain prompt whose quotes are checked afterwards by code**: no structured output, but the quotes must be found in free text by their quotation marks. ADR-0009 rejected that detection as brittle (nesting, English marks, apostrophes).
- **A hybrid: typed quotes checked by code, sentences without evidence ids, no sources message**: fits the answer on the sources and simplifies the schema. But it needs a new schema, a new prompt and a tryout, and it would ship a pipeline that never ran on the 16 articles. It also removes the ids just before inputs with several sources, where "which source says this" starts to matter. It stays the second branch of point 6.
- **Waiting for an eval set close to the outlet's content**: the only measure that would speak about real use. But the corpus does not record the sources of its articles, so such a set must be built with the journalists, after the 2026-11-22 target.
- **Adding a target length to the prompt now**: closer to the outlet's articles, but one source rarely supports 4,000 characters, so a target invites additions. It would also replace a frozen prompt with an unmeasured one.

## Consequences

- **Complexity is kept without a measured gain on the rate**: a structured schema, a validator and one retry. In the comparison v2 also stated fewer elements than the plain prompt (194 against 227) in slightly shorter articles.
- **Quotes will be few.** On the recorded drafts the guard leaves 11 quotes in 6 articles of 16, and 11 of the 22 frozen pages have no quotation mark at all, so they can give none.
- **The choice rests on one person's knowledge of the newsroom and on counters, not on the journalists' answers.** Point 6 is the check.
- **No arm was measured on the inputs the team is expected to send most.** The sample is press pages from two outlets. On a social media post the guard refuses every quote, so v2 gives none there, like the plain prompt, until its rule is decided.
- **A draft is about a third of the outlet's median length.** It is a starting point for the journalist, not a finished article.
- **Titles stay without quotation marks** (ADR-0012), although 128 of the outlet's 573 titles contain some. Not changed here.
- Falling back to the plain prompt later means moving its prompt from `evals/` to `src/` and adding a free-text rendering path; the fetching and wiring code would be reused as is.
- Next: fetching behind the SSRF guard, then worker and Telegram wiring, each with its own questions and options. To update when this ADR is accepted: the index, `docs/roadmap.md` and the "not measured here" limit of `docs/evals/v2-comparison.md`.
