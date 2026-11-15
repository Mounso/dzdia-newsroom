# ADR-0018: Pages are fetched through an SSRF guard placed under the HTTP client; pasted text is the way out when a link fails

- **Status:** Accepted
- **Date:** 2026-11-15
- **Deciders:** Abdou (author), with Claude as reviewer
- **Amends:** [ADR-0009](0009-v2-link-to-sourced-article-slice.md) (the scope: pasted text enters the slice; point 6: where the guard sits, limits, extraction; point 7: one more tool module; point 9: error types; the kata split for the guard)
- **Amended by:** [ADR-0019](0019-lightest-wiring-of-the-slice.md) (point 12: pasted text is named in the source line of the single reply)
- **Related:** [ADR-0002](0002-algerie-360-replaces-el-watan.md) (sites that refuse a plain client), [ADR-0014](0014-quote-must-be-quoted-in-source.md) (the quote guard), [ADR-0017](0017-v2-stays-the-production-path.md) (point 7: inputs are decided at this step); [independent review](../reviews/2026-11-13-independent-review.md), findings V-10, V-11, V-15, V-16; [security](../architecture/security.md)

## Context

ADR-0009, point 6, planned the download: resolve the host, refuse any address that is not global, connect to the checked address, follow redirects by hand, then extract the text with `extract_main_text`. The independent review found four gaps in that plan.

- **V-16:** connecting to a checked address is the easy half. TLS must still present and verify the original hostname, and the plan did not say how.
- **V-15:** `is_global` alone is not the rule. On the local interpreter it is true for multicast (`224.0.0.1`, `ff02::1`), for the NAT64 prefix (`64:ff9b::/96`) and for two deprecated IPv6 forms that embed an IPv4 address (`::/96`, `::ffff:0:0:0/96`).
- **V-10:** `extract_main_text` is an eval function. It needs the RSS headline, which production does not have, and it refuses any page whose HTML contains a paywall marker anywhere.
- **V-11:** fetch success on real sites is unmeasured. Four of six candidate outlets refused a plain client (ADR-0002), and ADR-0009 left a journalist with a blocked link no path.

Abdou's answers of 2026-11-15, as the person who knows the newsroom. No journalist has been asked yet.

- Journalists mostly send a link to a web page or to a video. Videos come first from Instagram and TikTok, then YouTube, Facebook and X.
- Pasted text is wanted as a way out when a link fails, not as a main input.
- The bot stays honest: it says that it failed. A mistake in the link gets a request to send it again; a failure on the bot's side gets a suggestion to paste the text.
- On a page cut by a paywall, the journalist should be warned.

Two facts were checked on the installed libraries (httpx 0.28.1, httpcore 1.0.9).

- httpcore opens every TCP connection through a network backend, then starts TLS with the hostname of the URL. A loopback spike confirmed that a wrapped backend receives the hostname and chooses the address, while the request keeps its `Host` header and its URL.
- `httpx.AsyncHTTPTransport` does not expose that backend. It can only be set through its private attribute `_pool`.

The kata rule of ADR-0009 is retired: the guard is written like any other code.

## Decision

1. **The guard is a network backend under the HTTP client** (`src/dzdia_newsroom/guardrails/ssrf.py`). It wraps httpcore's default backend. On every new TCP connection it resolves the hostname once, checks every resolved address (point 2), and connects to a checked address. A hostname with one refused address is refused entirely, as in ADR-0009. The URL is never rewritten, so SNI, certificate verification and the `Host` header use the original hostname with no extra code. Unix sockets are refused. The backend also refuses any port other than 80 and 443.
2. **Address rule.** An IPv4-mapped IPv6 address is first replaced by its IPv4 address. An address is then accepted only if `is_global` is true, it is not multicast, and it is not in an IPv6 prefix that embeds an IPv4 address: `64:ff9b::/96` (NAT64), `::/96` (IPv4-compatible) or `::ffff:0:0:0/96` (IPv4-translated). This refuses loopback, private, link-local (`169.254.169.254` included), carrier-grade NAT, `0.0.0.0` and the Docker service names, which resolve to private addresses.
3. **One guarded client for every address that code did not choose.** A factory in the same module returns an `httpx.AsyncClient` built on the guarded transport, with no proxy, no environment settings (`trust_env=False`), HTTP/1.1 only and no automatic redirects. Any request to a URL that comes from a journalist, a redirect or, later, a feed goes through it. The Telegram and Gemini clients keep their own client: their hosts are fixed in code.
4. **The junction with httpx is guarded twice.** The factory replaces the transport's private `_pool` with an `httpcore.AsyncConnectionPool` that uses the guarded backend, and raises at construction if the transport has no such pool. A test opens a loopback server and requires a refusal through the public factory: if a later httpx ignores `_pool`, the test fails. `httpcore` becomes a direct dependency.
5. **Redirects are followed by hand in the fetch tool**, at most 5. Every hop obeys the rule of the first URL: `http` or `https`, port 80 or 443, no user or password in the URL. A redirect from `https` to `http` is refused. The address of each hop is checked by the guard when the connection opens.
6. **Limits.** `text/html` and `application/xhtml+xml` only. The body is read as a stream and refused above 2 MB counted after decompression. One deadline of 15 s covers every hop, the body and the retry; the per-phase timeouts are 5 s. One retry after 1 to 2 s (1 s plus the jitter of the shared retry helper), only on a connection error, a timeout or a 502, 503 or 504 answer. A 401, 403 or 429 answer is a refusal and is never retried. Every fetch is logged with host, status, bytes, hops, attempts and latency.
7. **The bot names itself and does not work around a refusal.** The `User-Agent` is `Mozilla/5.0 (compatible; DzdiaNewsroomBot/<version>)`. No browser disguise, no proxy, no second try on a refusal. `robots.txt` is not read for a page that a journalist asks for. Automatic fetches (the morning briefing) decide their own rule in their ADR.
8. **Video and social links get a dedicated answer, with no fetch.** A link whose host is `instagram.com`, `tiktok.com`, `youtube.com`, `youtu.be`, `facebook.com`, `fb.watch`, `x.com` or `twitter.com`, or one of their subdomains, raises `UnsupportedLinkError`. The same check applies to every redirect hop, so a short link that lands there gets the same answer.
9. **Extraction shares its core with the eval** (`src/dzdia_newsroom/tools/page_text.py`). The trafilatura call with its measured parameters, `clean_text` and the paywall markers move there. Production reads the title from the page metadata, as `evals/v2/tryout.py` already does for a page with no feed title. `evals/baseline_v1/snapshot.extract_main_text` keeps its signature and its paywall refusal and calls the shared core. No snapshot is extracted again: the frozen texts and their hashes do not change.
10. **A page cut by a paywall still gives a draft, with a warning.** When a paywall marker is found in the HTML, production extracts the visible text and flags it. The reply starts with a line saying that the page seems reserved to subscribers and that the draft covers the visible part only.
11. **A source text has at least 300 characters.** A page whose extracted text is shorter raises `NoReadableTextError`. The same floor decides what counts as pasted text (point 12).
12. **Pasted text enters the slice as the way out.** A message with no link and at least 300 characters is a source text. It goes through the same pipeline from the evidence step: the 30,000-character cap, evidence items, generation. It is delimited as untrusted content like a page.
    - One message is one source. Messages are not joined. A text of 4,000 characters or more gets a notice that Telegram may have cut it and that the draft covers this message only.
    - When a message has a link, the link is processed and the rest of the text is ignored (ADR-0009, point 10).
    - The sources message names the source as pasted text; no URL is shown.
    - Only the failure messages of points 8 and 13 suggest pasting. Nothing prevents a journalist from pasting a text first.
    - The quote rule does not change: on a text written in the first person, no quote passes the guard (ADR-0017, point 7, stays open).
13. **Three families of errors** (amends ADR-0009, point 9). Each sends a short French message, then becomes `PermanentJobError`.
    - **The link:** `InvalidLinkError` (malformed, not `http` or `https`, wrong port, credentials in the URL), `BlockedUrlError` (refused by the guard; the message does not say why), `FetchError` with reason `not_found` (404, 410). The journalist is asked to check the link. `UnsupportedLinkError` has its own message, which suggests pasting the text or the caption.
    - **The site:** `FetchError` with reason `refused` (401, 403, 429), `unreachable` (connection error, timeout, other statuses), `too_many_redirects`, `too_large` or `not_html`, and `NoReadableTextError`. The message says that the site did not let the page be read and suggests pasting the text.
    - **Inside:** `GenerationFailedError` and any other error. A generic message, with no suggestion to paste: pasted text would fail the same way.
14. **Modules.** `guardrails/ssrf.py` (backend, address rule, client factory), `tools/fetch_page.py` (URL rule, redirects, limits, error types), `tools/page_text.py` (extraction). Tests never use the real network or real DNS: the resolver is injected and servers listen on loopback. The address tests run in CI on Python 3.12, the version of the runtime image.
15. **Left to the wiring step:** the French texts of the messages, how a link is found in a message, how the warning and the notices are rendered, and the `drafts` table.

## Alternatives considered

- **Pinning per request** (the URL targets the IP address, with a `Host` header and the `sni_hostname` extension): it stays on public APIs. But every call site must build it correctly, a pooled connection can be reused for another hostname on the same address, and `response.url` shows an IP address.
- **A hand-written transport on httpcore's public API**: no private attribute. But it copies the request and response mapping and the exception mapping of httpx, which are private too, and must follow them at each release.
- **An egress proxy**: rejected by ADR-0009; extra infrastructure for one slice.
- **A check before the request only**: rejected by ADR-0009; a second DNS answer can change the target.
- **Fetching video and social links like any page**: no host list to keep. But the draft could be written from a login wall or a cookie banner, and the journalist would not know why it is poor. Not tested.
- **Bringing video transcription into this slice**: it is what journalists send most, but it is a separate module (download, transcription, a quote rule for speech) and cannot land before 2026-11-22.
- **Pasted text only as a reply to the bot's failure message**: it keeps pasted text a strict fallback and links it to the failed URL. But a journalist who forgets to use "reply" gets nothing.
- **Joining messages that Telegram cut**: it accepts long articles. But it needs state between jobs and a waiting delay, which the slice does not have.
- **Asking first on a paywalled page** (draft or nothing): the journalist decides before the model is called. But the bot must remember the pending link and read the answer, which needs the workflow state that is not built yet. To reconsider with that state.
- **Refusing a paywalled page**: no partial draft. But the marker also matches menus, so whole sites would be refused.
- **A separate production extractor with its own settings**: free to tune. But production would run an extraction that no eval measured.
- **Reading `robots.txt` before each page**: stricter in form. But it costs one more request per link, and these files mostly name crawlers, not a bot that fetches one page for a person.

## Consequences

- **Most of what journalists send gets no draft in this slice.** A video link gets an immediate, honest answer and nothing else. This weighs on the order of the next stages, which is decided when stage 2 ends.
- **Fetch success on real sites is still unmeasured.** The server runs from a datacenter address and names itself, so refusals are expected. The fetch logs give the first count; pasted text is the way out.
- **A pasted article longer than one Telegram message is cut.** The bot says so, and the draft covers one message.
- **Pasted text is not locked as a fallback**: any long message without a link becomes a draft.
- **The paywall warning is a heuristic.** A marker in a menu gives a warning on a complete page; another wording gives none on a truncated one.
- **The production title comes from the page metadata**, while the 16 eval pages took theirs from the feed. The body extraction is the measured one.
- **The 300-character floor is not measured.** A short caption pasted alone gets the help text.
- **The guard depends on a private attribute of httpx.** Any httpx or httpcore update must pass the loopback refusal test. TLS towards a pinned address was read in httpcore's code and is not exercised by a test with a real certificate.
- **`robots.txt` is not read**, and this is stated in the security document.
- To update when this ADR is accepted: the status line of ADR-0009 and the index. With the code: `docs/architecture/security.md` (the SSRF row), `docs/architecture/overview.md`, `docs/roadmap.md` and `pyproject.toml` (`httpcore`).
- Next: worker and Telegram wiring, with the sources display (ADR-0017, point 7) and finding V-8.
