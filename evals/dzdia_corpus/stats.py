"""The `stats` step (ADR-0015, point 6): counters over the local post files, never a title, an
article text or an account name.

HTML is parsed with the standard library's `html.parser` into a small element tree (see
`parse_html`); every definition below works on that tree or on the body text.

Definitions (deterministic):

- Embed blockquote: a `blockquote` with the class `instagram-media`, `tiktok-embed` or
  `twitter-tweet` (`EMBED_CLASSES`). Its content is the embedded post, not article text: it is
  left out of the body text, the paragraphs, the headings and the italics below. Embed counts
  and Instagram accounts still read it.
- Body text: `content.rendered` parsed as HTML, the content of `script` and `style` elements and
  of embed blockquotes removed, the text content of the rest concatenated in document order,
  every whitespace run (no-break and other Unicode spaces included, as matched by `\\s`)
  collapsed to one space, stripped.
- Per post, outside embed blockquotes: characters of the body text; words, the body text split
  on single spaces (0 for an empty text); paragraphs, `<p>` elements whose own collapsed text is
  not empty; headings, `h2`, `h3` and `h4` elements; italics, whether the post has an `em` or `i`
  element.
- Quoted passages: spans of the body text between `«` and `»` containing neither mark (so the
  innermost span when marks nest), and separately between `“` and `”` containing neither mark.
  A span's length is the length of its inner text, stripped; a span longer than 1,500 characters
  is not counted (an unmatched mark). `« »` spans fall into length bands: under 40, 40 to 59,
  60 or more. The share of body characters inside `« »` spans is pooled over posts: the sum of
  the span lengths divided by the sum of the body text lengths.
- Titles: `title.rendered`, HTML-unescaped, containing any of `« » “ ” "`.
- Embeds, counted as posts having each: `iframe` by host of its `src` (lowercased, a leading
  `www.` removed; an iframe without a host in `src` is not counted), `blockquote` with the class
  `instagram-media`, `tiktok-embed` or `twitter-tweet`.
- Instagram embed: a `blockquote.instagram-media`, or an `iframe` whose host is `instagram.com`.
- Instagram accounts cited in a post: the first path segment of every `a[href]` whose host is
  `instagram.com` or `www.instagram.com`, lowercased, except `p`, `reel`, `reels`, `tv`,
  `explore`, `stories`, `accounts` and `share`; plus every `(@handle)` in the text of a
  `blockquote.instagram-media`, lowercased; minus `OWN_HANDLES`.
- Followed accounts (`--following`): the `username` column of a CSV file, stripped, a leading
  `@` removed, lowercased, empty values skipped.
- Quantiles of characters, words, paragraphs and headings: min, q25, median, q75, p90, max, with
  `statistics.median` for the median and `statistics.quantiles(..., method="inclusive")` for
  q25, q75 (n=4) and p90 (n=10); with a single post, every quantile is its value.
- Dates: the date part of `date_gmt`. Categories: names from `categories.json` (HTML-unescaped),
  `#<id>` for an id it does not hold; the 20 most frequent, ties by name.
"""

import csv
import html
import re
import statistics
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from evals.dzdia_corpus import CorpusError
from evals.dzdia_corpus.paths import CorpusPaths
from evals.dzdia_corpus.records import (
    WpPost,
    names_of,
    parse_post,
    post_ids,
    read_categories,
    read_json,
)

OWN_HANDLES = frozenset({"dzdia_", "dzdia"})
MAX_SPAN_CHARS = 1500
LONG_SPAN_CHARS = 60  # the "60 or more" band
MID_SPAN_CHARS = 40  # the "40 to 59" band starts here
TOP_CATEGORIES = 20
TITLE_QUOTE_MARKS = frozenset('«»“”"')
EMBED_CLASSES = ("instagram-media", "tiktok-embed", "twitter-tweet")
INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})
EXCLUDED_SEGMENTS = frozenset(
    {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "share"}
)

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param"}
    | {"source", "track", "wbr"}
)
_SKIPPED_TAGS = frozenset({"script", "style"})
_WHITESPACE = re.compile(r"\s+")
_GUILLEMET_SPAN = re.compile(r"«([^«»]*)»")
_CURLY_SPAN = re.compile(r"“([^“”]*)”")
_EMBED_HANDLE = re.compile(r"\(@([A-Za-z0-9._]+)\)")


# --- HTML tree ---


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list["Element | str"] = field(default_factory=list)

    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


class _TreeBuilder(HTMLParser):
    """A forgiving tree builder: void elements take no children, an end tag closes the nearest
    open element with that name (and those opened inside it), a stray end tag is ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, {name: value or "" for name, value in attrs})
        self._stack[-1].children.append(element)
        if tag not in _VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Element(tag, {name: value or "" for name, value in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse_html(markup: str) -> Element:
    builder = _TreeBuilder()
    builder.feed(markup)
    builder.close()
    return builder.root


def is_embed(element: Element) -> bool:
    """An embed blockquote (see the module docstring)."""
    return element.tag == "blockquote" and any(c in EMBED_CLASSES for c in element.classes())


def iter_elements(element: Element, *, skip_embeds: bool = False) -> Iterator[Element]:
    """Every element under `element` (itself excluded), in document order; with `skip_embeds`,
    embed blockquotes and everything inside them are left out."""
    for child in element.children:
        if isinstance(child, Element):
            if skip_embeds and is_embed(child):
                continue
            yield child
            yield from iter_elements(child, skip_embeds=skip_embeds)


def text_content(element: Element, *, skip_embeds: bool = False) -> str:
    """Concatenated text under `element`, `script` and `style` content left out; with
    `skip_embeds`, the content of embed blockquotes too."""
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in _SKIPPED_TAGS and not (skip_embeds and is_embed(child)):
            parts.append(text_content(child, skip_embeds=skip_embeds))
    return "".join(parts)


def collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def body_text(root: Element) -> str:
    """The article's body text, embed blockquotes left out (see the module docstring)."""
    return collapse(text_content(root, skip_embeds=True))


def _host(url: str) -> str:
    try:
        return (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


# --- per post ---


@dataclass(frozen=True)
class PostMetrics:
    chars: int
    words: int
    paragraphs: int
    headings: int
    has_italics: bool
    guillemet_spans: tuple[int, ...]  # inner lengths of the counted `« »` spans
    curly_spans: tuple[int, ...]  # inner lengths of the counted `“ ”` spans
    title_has_quotes: bool
    iframe_hosts: frozenset[str]
    blockquote_embeds: frozenset[str]  # among EMBED_CLASSES
    instagram_embed: bool
    accounts: frozenset[str]


def span_lengths(text: str, pattern: re.Pattern[str]) -> tuple[int, ...]:
    lengths = (len(m.group(1).strip()) for m in pattern.finditer(text))
    return tuple(n for n in lengths if n <= MAX_SPAN_CHARS)


def cited_accounts(root: Element) -> frozenset[str]:
    """Instagram accounts cited in a post, own handles excluded (see the module docstring)."""
    found: set[str] = set()
    for element in iter_elements(root):
        if element.tag == "a" and "href" in element.attrs:
            href = element.attrs["href"].strip()
            if _host(href) in INSTAGRAM_HOSTS:
                segment = urlsplit(href).path.lstrip("/").split("/")[0].lower()
                if segment and segment not in EXCLUDED_SEGMENTS:
                    found.add(segment)
        elif element.tag == "blockquote" and "instagram-media" in element.classes():
            found.update(h.lower() for h in _EMBED_HANDLE.findall(text_content(element)))
    return frozenset(found - OWN_HANDLES)


def post_metrics(content_html: str, title_html: str) -> PostMetrics:
    root = parse_html(content_html)
    text = body_text(root)
    elements = list(iter_elements(root))  # every element: embed detection
    article = list(iter_elements(root, skip_embeds=True))  # the body counters
    iframe_hosts = frozenset(
        host.removeprefix("www.")
        for e in elements
        if e.tag == "iframe" and (host := _host(e.attrs.get("src", "")))
    )
    embeds = frozenset(
        cls
        for e in elements
        if e.tag == "blockquote"
        for cls in e.classes()
        if cls in EMBED_CLASSES
    )
    title = html.unescape(title_html)
    return PostMetrics(
        chars=len(text),
        words=len(text.split(" ")) if text else 0,
        paragraphs=sum(
            e.tag == "p" and bool(collapse(text_content(e, skip_embeds=True))) for e in article
        ),
        headings=sum(e.tag in {"h2", "h3", "h4"} for e in article),
        has_italics=any(e.tag in {"em", "i"} for e in article),
        guillemet_spans=span_lengths(text, _GUILLEMET_SPAN),
        curly_spans=span_lengths(text, _CURLY_SPAN),
        title_has_quotes=any(c in TITLE_QUOTE_MARKS for c in title),
        iframe_hosts=iframe_hosts,
        blockquote_embeds=embeds,
        instagram_embed="instagram-media" in embeds or "instagram.com" in iframe_hosts,
        accounts=cited_accounts(root),
    )


# --- corpus ---


@dataclass(frozen=True)
class Quantiles:
    min: float
    q25: float
    median: float
    q75: float
    p90: float
    max: float


def quantiles(values: Sequence[int]) -> Quantiles:
    """See the module docstring; `values` must not be empty."""
    data = sorted(values)
    if len(data) == 1:
        v = float(data[0])
        return Quantiles(v, v, v, v, v, v)
    q25, _, q75 = statistics.quantiles(data, n=4, method="inclusive")
    p90 = statistics.quantiles(data, n=10, method="inclusive")[8]
    return Quantiles(
        min=float(data[0]),
        q25=float(q25),
        median=float(statistics.median(data)),
        q75=float(q75),
        p90=float(p90),
        max=float(data[-1]),
    )


@dataclass(frozen=True)
class QuoteStats:
    under_40: int  # `« »` spans, all posts
    from_40_to_59: int
    from_60: int
    posts_with_long: int  # posts with at least one `« »` span of 60+
    posts_with_3_long: int  # posts with at least three `« »` spans of 60+
    median_long_per_post: float
    share_inside: float  # pooled share of body characters inside `« »` spans
    curly_spans: int  # `“ ”` spans, all posts
    posts_with_curly: int


@dataclass(frozen=True)
class FollowingStats:
    followed: int  # size of the followed list
    cited_followed: int  # distinct cited accounts that are followed
    posts_citing_followed: int


@dataclass(frozen=True)
class CorpusStats:
    posts: int
    first_date: str
    last_date: str
    chars: Quantiles
    words: Quantiles
    paragraphs: Quantiles
    headings: Quantiles
    posts_with_italics: int
    quotes: QuoteStats
    titles_with_quotes: int
    iframe_hosts: tuple[tuple[str, int], ...]  # (host, posts), most frequent first
    blockquote_embeds: tuple[tuple[str, int], ...]  # (class, posts), in EMBED_CLASSES order
    posts_with_instagram_embed: int
    posts_citing_accounts: int
    distinct_accounts: int
    following: FollowingStats | None
    top_categories: tuple[tuple[str, int], ...]  # (name, posts)


def _date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError as exc:
        raise CorpusError(f"Invalid date_gmt in a post record: {value!r}") from exc


def _ranked(counts: Counter[str], limit: int | None = None) -> tuple[tuple[str, int], ...]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(ranked[:limit] if limit is not None else ranked)


def quote_stats(metrics: Sequence[PostMetrics]) -> QuoteStats:
    spans = [n for m in metrics for n in m.guillemet_spans]
    long_per_post = [sum(n >= LONG_SPAN_CHARS for n in m.guillemet_spans) for m in metrics]
    body_chars = sum(m.chars for m in metrics)
    return QuoteStats(
        under_40=sum(n < MID_SPAN_CHARS for n in spans),
        from_40_to_59=sum(MID_SPAN_CHARS <= n < LONG_SPAN_CHARS for n in spans),
        from_60=sum(n >= LONG_SPAN_CHARS for n in spans),
        posts_with_long=sum(k >= 1 for k in long_per_post),
        posts_with_3_long=sum(k >= 3 for k in long_per_post),
        median_long_per_post=float(statistics.median(long_per_post)),
        share_inside=sum(spans) / body_chars if body_chars else 0.0,
        curly_spans=sum(len(m.curly_spans) for m in metrics),
        posts_with_curly=sum(bool(m.curly_spans) for m in metrics),
    )


def following_stats(metrics: Sequence[PostMetrics], followed: frozenset[str]) -> FollowingStats:
    cited = frozenset().union(*(m.accounts for m in metrics))
    return FollowingStats(
        followed=len(followed),
        cited_followed=len(cited & followed),
        posts_citing_followed=sum(bool(m.accounts & followed) for m in metrics),
    )


def corpus_stats(
    posts: Sequence[WpPost],
    names: Mapping[int, str],
    followed: frozenset[str] | None = None,
) -> CorpusStats:
    if not posts:
        raise CorpusError("No post to count")
    metrics = [post_metrics(p.content.rendered, p.title.rendered) for p in posts]
    dates = sorted(_date(p.date_gmt) for p in posts)
    iframe_hosts = Counter(host for m in metrics for host in m.iframe_hosts)
    embeds = Counter(cls for m in metrics for cls in m.blockquote_embeds)
    categories = Counter(name for p in posts for name in names_of(p.categories, names))
    return CorpusStats(
        posts=len(posts),
        first_date=dates[0],
        last_date=dates[-1],
        chars=quantiles([m.chars for m in metrics]),
        words=quantiles([m.words for m in metrics]),
        paragraphs=quantiles([m.paragraphs for m in metrics]),
        headings=quantiles([m.headings for m in metrics]),
        posts_with_italics=sum(m.has_italics for m in metrics),
        quotes=quote_stats(metrics),
        titles_with_quotes=sum(m.title_has_quotes for m in metrics),
        iframe_hosts=_ranked(iframe_hosts),
        blockquote_embeds=tuple((cls, embeds[cls]) for cls in EMBED_CLASSES),
        posts_with_instagram_embed=sum(m.instagram_embed for m in metrics),
        posts_citing_accounts=sum(bool(m.accounts) for m in metrics),
        distinct_accounts=len(frozenset().union(*(m.accounts for m in metrics))),
        following=following_stats(metrics, followed) if followed is not None else None,
        top_categories=_ranked(categories, TOP_CATEGORIES),
    )


# --- files ---


def load_posts(paths: CorpusPaths) -> list[WpPost]:
    ids = post_ids(paths)
    if not ids:
        raise CorpusError(
            f"No local post in {paths.posts_dir}: run `python -m evals.dzdia_corpus fetch` first."
        )
    return [parse_post(read_json(paths.post_file(i))) for i in ids]


def read_following(path: Path) -> frozenset[str]:
    """The followed accounts of a CSV file with a `username` column (see the module docstring)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "username" not in reader.fieldnames:
                raise CorpusError(f"{path.name}: no `username` column")
            values = [row.get("username") or "" for row in reader]
    except OSError as exc:
        raise CorpusError(f"Cannot read the following file {path.name}: {exc.strerror}") from exc
    return frozenset(
        handle for v in values if (handle := v.strip().removeprefix("@").strip().lower())
    )


def compute(paths: CorpusPaths, following: Path | None = None) -> CorpusStats:
    followed = read_following(following) if following is not None else None
    return corpus_stats(load_posts(paths), read_categories(paths), followed)


# --- printing ---


def _num(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def _quantile_line(label: str, q: Quantiles) -> str:
    return (
        f"{label}: min {_num(q.min)}, q25 {_num(q.q25)}, median {_num(q.median)}, "
        f"q75 {_num(q.q75)}, p90 {_num(q.p90)}, max {_num(q.max)}"
    )


def stats_lines(s: CorpusStats) -> list[str]:
    """Counters only: no title, no article text, no account name."""
    q = s.quotes
    lines = [
        f"Posts: {s.posts} (date_gmt {s.first_date} to {s.last_date})",
        _quantile_line("Characters", s.chars),
        _quantile_line("Words", s.words),
        _quantile_line("Paragraphs", s.paragraphs),
        _quantile_line("Headings (h2-h4)", s.headings),
        f"Posts with italics (em, i): {s.posts_with_italics}",
        f"« » spans: {q.under_40} under 40 chars, {q.from_40_to_59} of 40-59, {q.from_60} of 60+",
        f"Posts with a « » span of 60+: {q.posts_with_long}; with three or more: "
        f"{q.posts_with_3_long}; median per post: {_num(q.median_long_per_post)}",
        f"Share of body characters inside « » spans: {q.share_inside:.1%}",
        f"“ ” spans: {q.curly_spans}, in {q.posts_with_curly} posts",
        f"Titles with quotation marks: {s.titles_with_quotes}",
        "Posts with an iframe, by host: "
        + (", ".join(f"{host} {n}" for host, n in s.iframe_hosts) or "none"),
        "Posts with a blockquote embed: "
        + ", ".join(f"{cls} {n}" for cls, n in s.blockquote_embeds),
        f"Instagram: {s.posts_with_instagram_embed} posts with an embed, "
        f"{s.posts_citing_accounts} posts citing an account, "
        f"{s.distinct_accounts} distinct accounts",
    ]
    if s.following is not None:
        f = s.following
        lines.append(
            f"Followed list of {f.followed} accounts: {f.cited_followed} cited accounts followed, "
            f"{f.posts_citing_followed} posts citing a followed account"
        )
    lines.append(
        f"Posts per category (top {TOP_CATEGORIES}): "
        + ", ".join(f"{name} {n}" for name, n in s.top_categories)
    )
    return lines
