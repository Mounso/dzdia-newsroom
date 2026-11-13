# Independent review — 2026-11-13

A second, independent review of dzdia-newsroom, written before ADR-0009 (the v2 slice) is coded. It was read-only: no existing file was changed, nothing was committed, and the human labels were not touched.

## Scope and method

- **Read in full:** `docs/STATUS.md`, `docs/README.md`, `docs/roadmap.md`, ADRs 0001 to 0009, `docs/evals/baseline-v1.md`, `evals/datasets/baseline_v1/` (report, sample, feeds, labels), all of `src/`, all of `evals/baseline_v1/`, `deploy/*.sh`, `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `.github/workflows/ci.yml`, `pyproject.toml`, `docs/architecture/security.md`. The 2026-10-28 design review was read last, after my own findings were listed, to mark what is already tracked.
- **Read partly:** the tests (every test name, plus the passages cited below), `docs/architecture/job-queue.md` (targeted search).
- **Not read:** `.env`, `.env.example`, the private product spec, `docs/fiche_de_suivi.md`, the operations and development guides, `dependabot.yml`, the systemd units.
- **Run:** `pytest` (174 passed, 13 skipped: the Postgres tests, which need a container); `mypy` strict (no issue in 65 files); `ruff check` and `ruff format --check` (clean, 96 files); three throwaway scripts that recompute the eval figures from the local data in `evals/.data/` and exercise two suspected defects. They wrote nothing to the repository.
- **Not run:** the Postgres suite (Docker was not running), anything against Telegram, Gemini or a VM.

Each finding gives a reference, a **severity** (High: changes a headline number or a decision; Medium: a realistic failure or a weak claim; Low: an edge case or a nuisance), my **confidence** that the finding is correct, and a **kind**: *Defect* (wrong or contradicts the project's own rules), *Risk* (may go wrong; needs a decision), or *Taste* (defensible either way). Findings already tracked in the earlier review are marked with its id.

## Verdict

1. The engineering is careful and honest for 17 days of work: decisions are recorded with their rejected alternatives, the queue is tested guarantee by guarantee, and the eval report can be rebuilt byte for byte from the data.
2. The project is, today, a well-built message queue and a small evaluation harness. `src/` contains no LLM call. For an AI-engineer portfolio the order of work is the main weakness, not the quality of what exists.
3. The v1 baseline is methodologically above what most portfolios show, but its headline is weaker than it reads: "28 % [21–35 %]" is the interval of a floor, the blend of two tasks set by the replication design, and mostly generic place names.
4. ADR-0009 has two design problems to fix before coding (the indirect-speech fallback and unverified speakers), and its eval can favor v2 by construction unless the labeling rule changes.
5. Two defects were confirmed by execution: one failed heartbeat cancels a running job, and the LLM client does not retry timeouts.

## What is done well

These are worth keeping and worth saying in an interview.

- **The eval is reproducible from its data.** `build_report` on the local records gives exactly the committed `report.md`. All 733 element ids are unique, the 252 labels all point to an existing element, every `not_found` element is labeled, and the audited `found` elements are exactly the hash-defined sample (119 of 600, 19.8 %).
- **The statistics are chosen for the data.** Resampling articles rather than elements ([`report.py:42-65`](../../evals/baseline_v1/report.py)), worst-case bounds while labels are pending (`report.py:99-111`), and content-derived element ids so that a re-extraction cannot move a label ([`extract.py:58-67`](../../evals/baseline_v1/extract.py)) are all correct and uncommon in a personal project.
- **Limitations are written down.** `docs/evals/baseline-v1.md:104-114` lists the weak points itself, including the floor.
- **The queue is small and its guarantees are tested**: fenced updates ([`repository.py:35`](../../src/dzdia_newsroom/db/repository.py)), attempts counted at claim time (`0001_jobs.sql:8-9`), release on shutdown without burning an attempt, a wake event cleared before draining ([`queue.py:106`](../../src/dzdia_newsroom/worker/queue.py)).
- **Security hygiene is real, not decorative**: settings split per process so the API never holds the bot token ([`config.py:53-84`](../../src/dzdia_newsroom/config.py)), constant-time secret check, fail-closed allowlist, token redaction, `httpx` request logs silenced because Bot API URLs contain the token ([`logging_setup.py:48-50`](../../src/dzdia_newsroom/logging_setup.py)), validation errors reduced to a count before they reach `last_error` ([`handlers.py:31-33`](../../src/dzdia_newsroom/worker/handlers.py)).
- **The backup design** (ADR-0007 point 6) is the most senior piece of the repository: add-only bucket through an instance principal, encryption to a key that is not on the host, and a restore test inside every backup.
- **ADRs record the alternatives that lost**, with a reason. ADR-0003 is the exception ("None recorded").

## 1. Architecture and ADRs

Most choices are justified for one VM, five users and one maintainer: two processes and a Postgres queue, raw SQL with numbered migrations, a hand-written Telegram client, one compose file with profiles, plain text to Telegram. I would not contest any of these. What I would contest:

| Id | Finding | Ref | Severity | Confidence | Kind |
|---|---|---|---|---|---|
| A-1 | **The order of work, not any single decision.** ADR-0007 and `deploy/` (579 lines of bash), digest pinning, Dependabot on four ecosystems and arm64 builds all exist for a bot whose only handler answers "Bien reçu." and that has never run on the VM. After 17 days the roadmap is still in week 1 of 5, and `src/` imports no LLM library. | `handlers.py:23`, `STATUS.md:12`, `roadmap.md:15` | High | High | Risk |
| A-2 | **ADR-0003: the reference text of a faithfulness eval was never read by a human.** The compensating control (check each `NOT_IN_SOURCE` on the live page) leaves no trace: none of the 252 notes says `absent du snapshot`. That may mean nothing was missing, or that the check was not done; the data cannot tell. Reading 16 short texts is about 30 minutes. | `0003:27`, `baseline-v1.md:69` | Medium | High | Defect |
| A-3 | **ADR-0008: the extractor's recall is unknown.** The probe compares element counts at two thinking levels (9, 3, 21, 5 against 7, 3, 24, 5), which is not recall. The model is from the same family as the model under test, and the choice was driven by the deadline. The control "the labeler notes missed facts" produced no such note among 18. | `0008:15-24`, `baseline-v1.md:70` | Medium | High | Risk |
| A-4 | **ADR-0007: a deployment is recorded `ok` when only the API answers.** `/health` returns a constant, and the worker has no check. A worker in a restart loop passes `deploy.sh`. This matters more with v2, which gives the worker new required settings. Tracked as R9, deferred again by ADR-0009. | `deploy.sh:47-53`, `app.py:30-32`, `0009:58` | Medium | High | Defect |
| A-5 | **ADR-0007 is unvalidated.** The ADR says so ("the real test is a run on the VM"). Until that run, the instance-principal upload, the `sshd` change and the firewall edit are designs. | `0007:76`, `STATUS.md:18` | Medium | High | Risk |
| A-6 | **ADR-0004: "about 150 lines" understates the queue.** It is 376 lines (`queue.py` 257, `repository.py` 119) and 516 lines of tests. Writing it was a reasonable learning choice, stated honestly ("good interview material"); in a team I would expect procrastinate or pgqueuer. | `0004:54` | Low | High | Taste |
| A-7 | **The pre-deployment backup is a hard gate with no bypass.** An Object Storage outage blocks a hotfix. | `deploy.sh:94-100,116` | Low | High | Taste |
| A-8 | **Every ADR says "Abdou (author), with Claude as reviewer"**, while `STATUS.md` records the opposite flow: Claude proposes and writes, Abdou validates. See section 5. | `0009:5`, `STATUS.md:167-168` | Medium | Medium | Risk |

## 2. ADR-0009, before it is coded

### Design problems

| Id | Finding | Ref | Severity | Confidence | Kind |
|---|---|---|---|---|---|
| V-1 | **The indirect-speech fallback publishes the text the guard just rejected.** A quote fails because its words are not in the evidence. The fallback keeps those words, attributes them to a named person, and removes the quotation marks. First-person text also reads wrong ("Selon X, mon père…"). In the baseline, 5 of the 7 unsupported `page_text` quotes are rewordings of 11 to 20 words: exactly what would be downgraded and kept. The eval would then miss them, because the extractor defines a quote by its quotation marks (`extract.py:27`). Counting downgrades (0009:54) is not checking them. Simpler and safer: drop the segment and say so in the sources message. | `0009:25-26` | High | High | Defect |
| V-2 | **The quote check verifies the words, not the speaker.** The labeling guide counts "a quote given to someone else" as `CONTRADICTED`. The eval will be blind to it: every v2 quote is an exact substring by construction, so the matcher marks it `found`, and only 20 % of `found` elements are labeled. ADR-0010 should require a label on every v2 quote. | `0009:25`, `baseline-v1.md:63`, `matcher.py:144-145`, `labeling.py:16` | High | High | Defect |
| V-3 | **Validation proves the citation format, not the support.** An unknown id, a missing id and a bad quote are caught; nothing checks that the cited paragraph supports the sentence. Of the 19 unsupported elements in `page_text / article`, 12 are not quotes (8 places, 2 dates, 1 person, 1 organization), and v2 targets them only through its prompt. Expect the quote rate to fall to about zero by construction and the main rate to move little. With 0 to 3 unsupported elements per article over 16 articles, a real but modest gain may not separate from zero. ADR-0010 should say now what the write-up will be in that case. | `0009:26,55` | High | Medium | Risk |
| V-4 | **The comparison metric favors v2 by construction.** Unlabeled `found` elements count as supported. In `page_text`, 4 of 83 audited `found` elements were contradicted; weighting them gives 9 % [4–15 %] against the reported 5 % [3–8 %], so most of the true errors are probably already in the bucket that is 20 % labeled. v2 moves even more of its output into that bucket. For the article task, label every element in both arms (roughly 185 more for `page_text / article`, about an hour at the pace of stage 1). | `report.py:68-75`, `report.md:13,43` | High | High | Defect |
| V-5 | **"Only the pipeline differs" is not accurate.** The prompt, the temperature (v1 ran with no config, `llm.py:180-181`) and structured output all change together, and the v1 prompt asks for an "engaging" article that "highlights talents and culture" (`v1_prompts.py:25-27`), which invites additions. A plain prompt saying "use only this text" may win most of the gain with no architecture at all. Without an ablation the result cannot be attributed to citations or to the validator. `STATUS.md:170` leaves the ablation open; I would make it mandatory (16 calls per arm). | `0009:33,53` | Medium | High | Risk |
| V-6 | **Temperature 0 may not do what the ADR assumes.** Google's current guide for its newest models (3.6 Flash, 3.5 Flash-Lite) says `temperature` is "deprecated and ignored". I could not confirm what applies to `gemini-3.1-flash-lite-preview`. Check before the protocol states "temperature 0". This also touches ADR-0008. | `0009:33`, `llm.py:201` | Medium | Low | Risk |
| V-7 | **The team group will not behave as designed.** By default a bot in a group receives only commands, mentions and replies (Telegram's privacy mode), so a plain link sent to the group never arrives. With privacy mode off, the bot answers every text message of every allowlisted journalist: "Bien reçu." today, the help text tomorrow. The ADR needs a rule for groups (for example: in a group, act only on a link, stay silent otherwise). Based on my knowledge of the Bot API, not tested here. | `webhook.py:56-61`, `0009:32` | Medium | Medium | Defect |
| V-8 | **The "immediate" acknowledgment is sent by the worker**, because the API has no token. With one job at a time, a second journalist's acknowledgment waits behind the first job. That job has no overall deadline: one model call can last 90 s, the client retries up to four times, and the validation retry doubles it. The acknowledgment is also sent again on every attempt, since only the article has a `sent_at`. | `0009:32`, `queue.py:124-130`, `llm.py:95,151` | Medium | High | Risk |
| V-9 | **The retry loop's failure list is incomplete** (specification only; the loop is Abdou's kata). The ADR lists semantic errors. It does not say what happens with invalid or truncated JSON, an empty response after a safety or recitation block, or a timeout, nor whether these consume the single retry. Today a parse failure raises outside the retry helper (`llm.py:211`). | `0009:26` | Medium | High | Defect |
| V-10 | **`extract_main_text` is an eval function, not a product one.** It requires the RSS headline, which production does not have; it refuses any page whose HTML contains "réservé aux abonnés" anywhere, menus included; and it strips a line pattern specific to Algérie 360. For the eval, v2 must also receive exactly the frozen snapshot text, or the model's input and the labeling reference diverge. | `snapshot.py:29,42,45-57`, `0009:28` | Medium | Medium | Risk |
| V-11 | **Fetch success on real sites is unmeasured, and the eval cannot show it** (it replays stored HTML). ADR-0002 records that four of six candidate outlets refused a plain client; a datacenter address will do worse. Pasted text is out of scope, so a journalist with a blocked link has no path. Pasted text is the cheapest feature of all (no fetch, no SSRF) and would make the slice usable when fetching fails. | `0002:10-14`, `0009:10` | Medium | Medium | Risk |

### Schedule and simplifications

| Id | Finding | Ref | Severity | Confidence | Kind |
|---|---|---|---|---|---|
| V-12 | **The measured result comes last and depends on everything.** The eval needs only evidence, generation and validation; it replays snapshots, so it needs neither the SSRF guard, nor `drafts`, nor Telegram. Stage 1 slipped by two days. Do step 4, then step 6, then steps 3 and 5: the number for the CV is safe even if the wiring runs late. | `STATUS.md:169-174` | High | High | Risk |
| V-13 | **`drafts` could wait.** A replay is rare and a duplicate call on a lite model costs a fraction of a cent; the "Bien reçu." handler already accepts duplicates. If the table stays, note that one `sent_at` covers several messages, each with waits of up to 30 s, so the duplicate window is not "small" (0009:56); and add what the guard did (retry used, quotes dropped, errors), or its firing rate in production stays invisible. | `0009:30`, `telegram.py:104` | Low | High | Taste |
| V-14 | **Two worker replicas would solve V-8 without new code.** The queue already supports it (`SKIP LOCKED`, fencing). The request spacer is per process, which is fine at this volume. | `repository.py:15-31` | Low | Medium | Taste |

### Points for the kata specifications (no code)

| Id | Finding | Ref | Severity | Confidence | Kind |
|---|---|---|---|---|---|
| V-15 | **`is_global` alone is not the whole rule.** On the local interpreter it is true for multicast (`224.0.0.1`) and for the NAT64 prefix (`64:ff9b::/96`). Neither is exploitable on this host today, but both belong in the test list, with IPv4-mapped IPv6, `0.0.0.0`, CGNAT and the Docker service names `postgres` and `api`. Run those tests on the production Python: the local virtualenv is 3.14.3, the image is 3.12.16, and there is no `.python-version`. | `0009:28`, `Dockerfile:10` | Low | High | Risk |
| V-16 | **The hard part of "connect to the checked address" is not mentioned**: TLS must still present and verify the original hostname. The tests should also cover the size limit after decompression, a total deadline (httpx timeouts are per phase), and a redirect that changes scheme or port. | `0009:28` | Medium | Medium | Risk |
| V-17 | **Evidence can contain the closing delimiter.** The same pattern is already unescaped in the extraction prompt. Also, a quote that straddles a 600-character split can never validate, since a quote cites one id: the splitter must not cut inside quotation marks. | `extract.py:39-41`, `0009:27` | Low | Medium | Risk |
| V-18 | **Check the union schema with a ten-line spike first.** Google's guide shows `anyOf` working, but warns that unions and optional fields make the output grammar more complex, and the client still uses `response_schema`. | `0009:24`, `llm.py:198-203` | Low | Low | Risk |

## 3. Rigor of the v1 evaluation

The protocol is sound. The problems are in how the figures are presented, and in controls that are claimed but leave no evidence.

| Id | Finding | Ref | Severity | Confidence | Kind |
|---|---|---|---|---|---|
| E-1 | **"At least 28 % [21–35 %]" prints the interval of the floor, not of the rate.** A reader takes 35 % as the upper limit of the truth. The project's own extrapolation (36 %) is outside the printed interval. Weighting the audited `found` elements by their sampling rate, with the same cluster bootstrap, gives **36 % [23–53 %]** on the same data. Either print the floor with its lower limit only ("at least 28 %, and at least 21 % with 95 % confidence"), or label the 150 unaudited elements and publish a real estimate. The CV line in `STATUS.md:9` has the same flaw. | `baseline-v1.md:97,110`, `report.md:11` | High | High | Defect |
| E-2 | **The headline is a blend set by the replication design.** `url_only` is 9 % on summaries and 48 % on articles; 28 % comes from running three summaries per article. Change `REPEATS` and the headline changes. Report by task; the article task is the one the product is about. | `report.md:9-11`, `runner.py:12` | High | High | Defect |
| E-3 | **"Multiplies by about 5" compares rates with different denominators.** Over the same 64 outputs the count of unsupported elements goes from 24 to 72, a factor of 3. `url_only` articles state half as many checkable elements (7.9 against 16.0 per article): vagueness lowers exposure. Related: the metric sees entities only, so an unsupported claim with no name or number is invisible. That limit is not in the list. | `baseline-v1.md:100` | Medium | High | Defect |
| E-4 | **The slug result rests on four articles.** One of them (`tsa-05-opaque`) carries 13 of the 21 unsupported elements and half of all elements; another has 2 elements. A bootstrap over four clusters is not a 95 % interval. What the data supports: the rate rises in 4 articles out of 4, and the sample is too small to quantify it. | `report.md:15-16`, `baseline-v1.md:101` | Medium | High | Defect |
| E-5 | **The labeler is not blind.** The sheet shows the condition and the matcher's verdict and score; the only labeler is the project's author; the quote rule was set after seeing the data; 13 labels then changed after checks whose scripts were not kept. For v2: hide those columns, mix the arms, freeze the rules first, keep the scripts. | `labeling.py:18-21`, `baseline-v1.md:68,88`, `STATUS.md:10` | Medium | High | Defect |
| E-6 | **Places drive the number.** 45 of the 72 unsupported `url_only` elements are places, led by Algérie (7), Alger, Oran and Constantine (6 each): generic names added by prompts that ask for an Algerian cultural angle. This is correct under the definition in ADR-0001, but an invented city weighs as much as an invented quote. Give the rate with and without places. | `report.md:24`, `v1_prompts.py:13-14` | Medium | High | Taste |
| E-7 | **Zero-width intervals are printed as if informative**: `0% [0%–0%] (0/126)`, `(0/3)`, and `30% [0%–100%] (3/10)`. A percentile bootstrap cannot give a width when the count is zero, and a test pins that behavior. With 0 of 126, the upper limit is about 2.4 %. Print "too few" under a threshold. | `report.md:10,25,26`, `test_report.py:22-23` | Medium | High | Defect |
| E-8 | **The figures cannot be rebuilt from the repository.** `labels.jsonl` is committed, but the elements, runs and extractions it refers to live only in `evals/.data/` on one machine, with no backup mentioned. A third party "fetching the pages again" would measure a new sample, not reproduce this one. Commit a skeleton without text (ids, type, condition, task, match status). | `paths.py:46-55`, `baseline-v1.md:128` | Medium | High | Defect |
| E-9 | **Reference never read; extractor recall unknown.** See A-2 and A-3. | | Medium | High | Defect |
| E-10 | **Sample independence.** The 16 articles were published within six hours on one day by two outlets, and two pairs cover the same story. I checked: merging the pairs moves the interval from [20.9–35.4] to [20.5–35.8], so this does not matter. The external-validity limit is the real one, and it is stated. | `sample.jsonl:2,5,11,15` | Low | High | Risk |
| E-11 | **Three labels to re-read (not changed by me).** Their notes read oddly against the guide: `algerie360-00:page_text:article:0#place:a8f084154f` (`S`, but the note describes a different relation), `algerie360-04:page_text:summary:0#organization:b828a3b06d` (`S`, justified by outside knowledge), `algerie360-02:page_text:article:0#organization:f96d8e8b2b` (`S`, by equivalence; defensible). At most 2 of 445. | `labels.jsonl` | Low | Medium | Risk |
| E-12 | **Stale public text.** The README still says the baseline is "in progress" and counts "20 real articles"; the report header cites "ADR-001" (tracked as B9). The README is what a recruiter reads first, and it carries no result. | `README.md:9,13`, `report.md:3` | Low | High | Defect |

## 4. Code and tests

| Id | Finding | Ref | Severity | Confidence | Kind |
|---|---|---|---|---|---|
| C-1 | **One failed heartbeat cancels the job and uses an attempt.** The comment and the documentation say two beats can be missed. If `extend_lease` raises (a pool timeout, a dropped connection), the handler is cancelled at once and the job goes to retry, although the lease is still valid. Confirmed by running the worker against a store whose first heartbeat raises. No test covers it: the fake store only returns `False`. With v2, a brief database error during a 60-second model call throws the generation away. A lost lease and an unanswered question are two cases. | `queue.py:54,216-228`, `job-queue.md:66`, `test_queue.py:232-245` | Medium | High | Defect |
| C-2 | **The LLM client does not retry timeouts or connection errors.** Only API errors with a status code are retryable; `httpx.ReadTimeout` made one attempt in my run. The SDK's own retries are opt-in and not configured. The client also logs nothing, against the project rule that every external call is logged with cost and latency. The 90 s timeout sits 2 s above the longest latency observed. | `llm.py:123-124,157-159`, `STATUS.md:84` | Medium | High | Defect |
| C-3 | **A parse failure would copy model output into `jobs.last_error`.** `model_validate_json` raises a Pydantic error whose text includes the input, and the worker stores `str(exc)`. The handler already avoids this for payloads; the v2 path needs the same care. | `llm.py:211`, `queue.py:81-82` | Low | High | Risk |
| C-4 | **`write_jsonl` truncates in place.** It rewrites `labels.jsonl`, the one file that cannot be regenerated, and `elements.jsonl` after every extraction. A crash in the middle loses the file. Write to a temporary file, then replace. | `jsonl.py:23-27`, `__main__.py:172,218` | Low | High | Defect |
| C-5 | **`import-labels` accepts unknown element ids** and overwrites earlier labels silently. An id damaged by a spreadsheet becomes an orphan the report ignores. There are none today. | `labeling.py:69-77`, `__main__.py:214-219` | Low | High | Defect |
| C-6 | **Fuzzy names accept a one-letter difference** ("Marc Dupond" matches "Marc Dupont" at 91). For people that can be another person, and such a `found` is labeled only one time in five. | `matcher.py:22,125-131` | Low | Medium | Risk |
| C-7 | **Two runtime dependencies are used only by the evals** (`feedparser`, `rapidfuzz`), and today `src/` uses neither `trafilatura` nor `google-genai`. They ship in the production image. Move the eval-only ones to a dependency group. | `pyproject.toml:9,16` | Low | High | Defect |
| C-8 | **`STATUS.md` is committed with a Telegram user id**, a description of where the secrets are kept, and notes on who writes what. Remove the id before the repository is public. | `STATUS.md:71` | Low | High | Defect |
| C-9 | **The CLI that produced the numbers is untested** (`__main__.py`, 285 lines of glue), and so are the entry points, which explains the 69 % coverage. The reproduction above stands in for a test once; it is not one. | `evals/baseline_v1/__main__.py` | Low | Medium | Risk |

**Tests overall.** They are behavior-named, fast (6 s) and use injected clocks and fakes rather than patches. The queue, the webhook and the Telegram client are well covered. The gaps are C-1, C-9, and the absence of any test that feeds the pipeline hostile or malformed model output, which v2 will need.

## 5. What the project shows for an AI-engineer role

### What it demonstrates

- **Evaluation method**, the strongest part: a defined unit, a frozen sample, human labels, a matcher audited by sampling, clustered intervals, stated limits. Few candidates can explain why they resampled articles and not elements.
- **Reliability engineering**: at-least-once delivery, leases, fencing, backoff that respects a server's `retry-after`, graceful shutdown.
- **Security judgment**: least privilege per process, untrusted content treated as data, an SSRF design that names DNS rebinding.
- **Operations**: backups that are tested on every run, a rollback rule for migrations, pinned supply chain.
- **Decision discipline**: nine ADRs, a tracked review, a habit of measuring first.

### What is missing

- **Any LLM feature in the product.** No generation, retrieval, embedding, tool call or agent loop exists in `src/`.
- **A prompt Abdou has iterated against an eval.** The only prompts are the frozen v1 ones and one extraction prompt at its first version.
- **The six katas.** None is written yet, and they are the parts meant to be his own code.
- **Retrieval, an LLM judge, tracing, cost tracking**: all planned for weeks 3 to 5.
- **Real use.** No deployment, no journalist, no feedback.

### What would be overrated on a CV

| Claim | Why it would not survive an interview | Honest version |
|---|---|---|
| "Built an AI newsroom assistant" | The bot answers "Bien reçu." | "Built the runtime and the evaluation harness of a newsroom assistant; first AI feature in progress" |
| "Deployed on Oracle Cloud" | The scripts have never run on a VM | "Wrote and container-tested the provisioning, deployment and backup scripts" |
| "Cut hallucinations from 28 % to 5 %" | The 5 % is the old prompt given the page text, not a new system; both figures are floors | "Measured that not opening the link leaves at least 48 % of an article's factual elements unsupported, against 7 % when the page text is given" |
| "28 % [21–35 %]" as a rate | It is the interval of a floor (E-1) and a blend (E-2) | Per task, as a floor, or the corrected estimate with its wide interval |
| "RAG, agents, MCP" | Roadmap only | Leave out until built |
| "Production-grade job queue" | Never under real load; one worker | "Postgres job queue with leases and fencing tokens, tested against a real database" |
| Sole authorship | `STATUS.md` and `CLAUDE.md`, both committed, show that an AI agent wrote most of the code and drafted the ADRs | "Designed and directed with an AI coding agent; I made the decisions, labeled the data and wrote X" |

The last row matters most. Working this way is a real skill, and the repository shows it honestly, but the interviewer will open the queue's claim query, the bootstrap or the bounds and ask for them on a whiteboard. Each item on the CV should be something Abdou can rebuild and defend without the repository. The katas are the right instrument for that, and none is done.

## Priorities

1. **Fix V-1 and V-2 in ADR-0009** before any code: drop a failed quote instead of rewording it; label every v2 quote for its speaker.
2. **Write ADR-0010 around V-3, V-4 and V-5**: label all article-task elements in both arms, make the ablation mandatory, and state in advance what will be written if the main rate does not move.
3. **Reorder stage 2 (V-12)**: generation and eval first, fetching and Telegram after.
4. **Restate the v1 headline (E-1, E-2, E-4)** per task and as a floor, in `baseline-v1.md`, the README and the CV line.
5. **Fix C-1 and C-2** when the client moves to `src/llm/`: both are hit by the first long model call.
6. **Blind the next labeling round (E-5)** and commit a text-free skeleton of the elements (E-8).
7. **Decide the group rule (V-7)** and check the temperature question (V-6).
8. **Update the README (E-12)** with the result and the true status.

## Appendix: figures recomputed for this review

All from the local records, original items only unless stated.

| Check | Result |
|---|---|
| `build_report` against committed `report.md` | Identical |
| Elements, unique ids, labels, orphan labels | 733, 733, 252, 0 |
| `url_only`: `found` / `not_found` | 185 / 73 (67 unsupported, 6 supported) |
| `url_only`: audited `found` | 35: 30 supported, 4 contradicted, 1 not in source |
| `page_text`: audited `found` | 83: 79 supported, 4 contradicted |
| `url_only` unsupported by type | place 45, quote 12, organization 8, person 4, date 3, number 0 (of 72) |
| `url_only` unsupported by task | summary 12/132, article 60/126 |
| `page_text / article` unsupported by type | place 8, quote 7, date 2, person 1, organization 1 (of 19) |
| Audit-weighted rate, `url_only` | 36.2 % [22.5–53.4 %] (4,000 resamples of articles) |
| Audit-weighted rate, `page_text` | 8.9 % [4.3–15.3 %] |
| Floor with same-story pairs merged (14 clusters) | 27.9 % [20.5–35.8 %] |
| Slug, originals, per article | 3/7, 2/15, 1/16, 6/16 |
| Slug, opaque, per article | 3/6, 2/2, 3/7, 13/15 |
| Elements per article output | `url_only` 7.9, `page_text` 16.0 |
| Worker, first heartbeat raises once | Handler cancelled, `retry_later` called |
| `retry_async` on `httpx.ReadTimeout` | 1 attempt |
