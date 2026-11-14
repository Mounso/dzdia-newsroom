"""The `stats` step of the Dzdia corpus (ADR-0015, point 6): each definition on small
hand-written HTML, the corpus counters, the `--following` counts, the CLI output (counters only)
and the "no local posts" error."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals.dzdia_corpus import CorpusError
from evals.dzdia_corpus import __main__ as corpus_main
from evals.dzdia_corpus.paths import CorpusPaths
from evals.dzdia_corpus.records import WpPost, write_json_atomic
from evals.dzdia_corpus.stats import (
    Quantiles,
    body_text,
    corpus_stats,
    parse_html,
    post_metrics,
    quantiles,
    read_following,
)

NBSP = chr(0x00A0)  # no-break space
NNBSP = chr(0x202F)  # narrow no-break space (French typography)
BOM = chr(0xFEFF)  # as written by spreadsheet exports


def _record(
    post_id: int,
    content: str,
    *,
    title: str = "Titre",
    date: str = "2026-10-01T09:00:00",
    categories: tuple[int, ...] = (1,),
) -> dict[str, Any]:
    return {
        "id": post_id,
        "link": f"https://dzdia.com/a-{post_id}/",
        "date_gmt": date,
        "modified_gmt": date,
        "title": {"rendered": title},
        "content": {"rendered": content, "protected": False},
        "author": 7,
        "tags": [],
        "categories": list(categories),
    }


def _post(post_id: int, content: str, **kwargs: Any) -> WpPost:
    return WpPost.model_validate(_record(post_id, content, **kwargs))


def _quote(n: int) -> str:
    return "« " + "a" * n + " »"


# --- body text and per-post structure ---


def test_body_text_drops_script_and_style_and_collapses_unicode_spaces() -> None:
    markup = (
        f"<p>Un&nbsp;{NBSP} deux{NNBSP}trois</p><script>var x = 'cache';</script>"
        "<style>p { color: red }</style>\n\n<p>\tquatre <b>cinq</b></p>"
    )
    assert body_text(parse_html(markup)) == "Un deux trois quatre cinq"
    metrics = post_metrics(markup, "T")
    assert metrics.chars == len("Un deux trois quatre cinq")
    assert metrics.words == 5


def test_empty_content_has_no_words() -> None:
    metrics = post_metrics("<script>x()</script>", "T")
    assert (metrics.chars, metrics.words, metrics.paragraphs) == (0, 0, 0)


def test_paragraphs_headings_and_italics() -> None:
    markup = (
        f"<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5>"
        f"<p>texte</p><p> &nbsp;{NBSP} </p><p><img src='x.jpg'></p><p><br/></p><p><b>gras</b></p>"
    )
    metrics = post_metrics(markup, "T")
    assert metrics.paragraphs == 2
    assert metrics.headings == 3
    assert not metrics.has_italics
    assert post_metrics("<p><em>a</em></p>", "T").has_italics
    assert post_metrics("<p><i>a</i></p>", "T").has_italics


# --- quoted passages ---


def test_guillemet_span_with_nested_tags_and_no_break_spaces() -> None:
    markup = f"<p>Il a dit «{NBSP}Nous <em>reviendrons</em>,<br> <a href='#'>promis</a>{NBSP}».</p>"
    assert post_metrics(markup, "T").guillemet_spans == (len("Nous reviendrons, promis"),)


def test_band_edges_and_curly_quotes_counted_separately() -> None:
    markup = "<p>" + " ".join(_quote(n) for n in (39, 40, 59, 60)) + " “ curly text ”</p>"
    metrics = post_metrics(markup, "T")
    assert metrics.guillemet_spans == (39, 40, 59, 60)
    assert metrics.curly_spans == (len("curly text"),)

    stats = corpus_stats([_post(1, markup)], {})
    q = stats.quotes
    assert (q.under_40, q.from_40_to_59, q.from_60) == (1, 2, 1)
    assert (q.curly_spans, q.posts_with_curly) == (1, 1)


def test_nested_and_overlong_spans() -> None:
    nested = "<p>« dehors « dedans » fin »</p>"
    assert post_metrics(nested, "T").guillemet_spans == (len("dedans"),)
    assert post_metrics("<p>" + _quote(1500) + "</p>", "T").guillemet_spans == (1500,)
    assert post_metrics("<p>" + _quote(1501) + "</p>", "T").guillemet_spans == ()


def test_quote_counters_over_posts() -> None:
    three_long = "<p>" + " ".join(_quote(n) for n in (60, 70, 80)) + "</p>"
    one_long = "<p>" + _quote(65) + " " + _quote(10) + "</p>"
    none = "<p>pas de citation</p>"
    posts = [_post(1, three_long), _post(2, one_long), _post(3, none)]

    q = corpus_stats(posts, {}).quotes

    assert (q.posts_with_long, q.posts_with_3_long) == (2, 1)
    assert q.median_long_per_post == 1.0  # counts 3, 1, 0
    chars = [post_metrics(p.content.rendered, "T").chars for p in posts]
    assert q.share_inside == pytest.approx((60 + 70 + 80 + 65 + 10) / sum(chars))


# --- titles ---


@pytest.mark.parametrize(
    ("title", "has_quotes"),
    [
        ("&laquo;&nbsp;Le retour&nbsp;&raquo; de Souad", True),
        ("&#171;Retour&#187;", True),
        ("Le &#8220;retour&#8221;", True),
        ("Un &quot;retour&quot;", True),
        ("Retour d&rsquo;Idir", False),
    ],
)
def test_titles_with_quotation_marks_are_unescaped(title: str, has_quotes: bool) -> None:
    assert post_metrics("<p>x</p>", title).title_has_quotes is has_quotes


# --- embeds ---


def test_iframe_hosts_are_normalized_and_counted_once_per_post() -> None:
    first = (
        "<iframe src='https://www.YouTube.com/embed/a'></iframe>"
        "<iframe src='https://youtube.com/embed/b'></iframe>"
        "<iframe src='//player.vimeo.com/video/1'></iframe>"
        "<iframe data-src='https://lazy.example/x'></iframe><iframe src='about:blank'></iframe>"
    )
    second = "<iframe src='https://www.youtube.com/embed/c'></iframe>"

    assert post_metrics(first, "T").iframe_hosts == {"youtube.com", "player.vimeo.com"}
    stats = corpus_stats([_post(1, first), _post(2, second)], {})
    assert stats.iframe_hosts == (("youtube.com", 2), ("player.vimeo.com", 1))


def test_blockquote_embeds() -> None:
    markup = (
        "<blockquote class='instagram-media' data-instgrm-version='14'><p>x</p></blockquote>"
        "<blockquote class='tiktok-embed'>t</blockquote>"
        "<blockquote class='twitter-tweet'>t</blockquote><blockquote class='other'>o</blockquote>"
    )
    posts = [_post(1, markup), _post(2, "<blockquote class='twitter-tweet'>t</blockquote>")]

    stats = corpus_stats(posts, {})

    assert stats.blockquote_embeds == (
        ("instagram-media", 1),
        ("tiktok-embed", 1),
        ("twitter-tweet", 2),
    )
    assert stats.posts_with_instagram_embed == 1
    iframe = "<iframe src='https://www.instagram.com/p/X/embed'></iframe>"
    assert post_metrics(iframe, "T").instagram_embed


ARTICLE_HTML = "<h2>Titre</h2><p>Le texte de l’article, « une citation courte ».</p>"


def _body_counters(markup: str) -> tuple[object, ...]:
    m = post_metrics(markup, "T")
    return (m.chars, m.words, m.paragraphs, m.headings, m.has_italics, m.guillemet_spans)


def test_instagram_embed_text_is_not_article_text() -> None:
    embed = (
        "<blockquote class='instagram-media' data-instgrm-version='14'><div>"
        "<h3>En-tête</h3><p>Légende « " + "b" * 70 + " » avec <em>style</em></p>"
        "<p><a href='https://www.instagram.com/p/ABC/'>Une publication partagée par Some One "
        "(@Embed.Artist)</a></p></div></blockquote>"
        "<script async src='//www.instagram.com/embed.js'></script>"
    )
    with_embed = post_metrics(ARTICLE_HTML + embed, "T")

    assert _body_counters(ARTICLE_HTML + embed) == _body_counters(ARTICLE_HTML)
    assert with_embed.chars == len("TitreLe texte de l’article, « une citation courte ».")
    assert (with_embed.paragraphs, with_embed.headings, with_embed.has_italics) == (1, 1, False)
    assert with_embed.blockquote_embeds == {"instagram-media"}
    assert with_embed.instagram_embed
    assert with_embed.accounts == {"embed.artist"}
    stats = corpus_stats([_post(1, ARTICLE_HTML + embed)], {})
    assert stats.quotes.from_60 == 0 and stats.posts_with_instagram_embed == 1


def test_tweet_embed_with_a_long_quote_adds_no_span() -> None:
    tweet = (
        "<blockquote class='twitter-tweet'><p lang='fr'>« "
        + "c" * 65
        + " »</p>&mdash; Quelqu’un (@someone)</blockquote>"
    )
    metrics = post_metrics(ARTICLE_HTML + tweet, "T")

    assert metrics.guillemet_spans == (len("une citation courte"),)
    assert _body_counters(ARTICLE_HTML + tweet) == _body_counters(ARTICLE_HTML)
    assert metrics.blockquote_embeds == {"twitter-tweet"}
    assert metrics.accounts == frozenset()  # (@handle) is read in Instagram embeds only


# --- Instagram accounts ---

ACCOUNTS_HTML = (
    "<p><a href='https://www.instagram.com/Artist_One/?hl=fr'>lien</a>"
    "<a href='https://instagram.com/p/XYZ/'>post</a><a href='https://instagram.com/reel/R/'>r</a>"
    "<a href='https://instagram.com/reels/R/'>r</a><a href='https://instagram.com/tv/R/'>r</a>"
    "<a href='https://instagram.com/explore/tags/x/'>e</a>"
    "<a href='https://instagram.com/stories/someone/1/'>s</a>"
    "<a href='https://instagram.com/accounts/login/'>a</a>"
    "<a href='https://instagram.com/share/abc/'>s</a>"
    "<a href='https://www.instagram.com/dzdia_/'>nous</a>"
    "<a href='https://notinstagram.com/fake/'>x</a><a href='https://instagram.com'>home</a></p>"
    "<blockquote class='instagram-media'><a href='https://www.instagram.com/p/ABC/'>"
    "Une publication partagée par Some One (@Second.Artist)</a> (@dzdia)</blockquote>"
    "<p>Hors embed (@not_counted)</p>"
)


def test_instagram_accounts_from_links_and_embeds() -> None:
    assert post_metrics(ACCOUNTS_HTML, "T").accounts == {"artist_one", "second.artist"}


def test_instagram_counters_and_following(tmp_path: Path) -> None:
    other = "<p><a href='https://instagram.com/third/'>x</a></p>"
    posts = [_post(1, ACCOUNTS_HTML), _post(2, other), _post(3, "<p>rien</p>")]
    csv_path = tmp_path / "following.csv"
    csv_path.write_text(
        BOM + "username,full_name\n@Artist_One,A\nthird,T\nnobody_cited,N\n,Empty\n",
        encoding="utf-8",
    )

    followed = read_following(csv_path)
    stats = corpus_stats(posts, {}, followed)

    assert followed == {"artist_one", "third", "nobody_cited"}
    assert (stats.posts_with_instagram_embed, stats.posts_citing_accounts) == (1, 2)
    assert stats.distinct_accounts == 3
    assert stats.following is not None
    assert stats.following.followed == 3
    assert stats.following.cited_followed == 2
    assert stats.following.posts_citing_followed == 2
    assert corpus_stats(posts, {}).following is None


def test_following_without_username_column_fails(tmp_path: Path) -> None:
    csv_path = tmp_path / "following.csv"
    csv_path.write_text("handle\nx\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="username"):
        read_following(csv_path)
    with pytest.raises(CorpusError, match="Cannot read"):
        read_following(tmp_path / "missing.csv")


# --- quantiles, dates, categories ---


def test_quantiles_are_inclusive() -> None:
    assert quantiles([5, 1, 3, 2, 4]) == Quantiles(1.0, 2.0, 3.0, 4.0, 4.6, 5.0)
    assert quantiles([7]) == Quantiles(7.0, 7.0, 7.0, 7.0, 7.0, 7.0)


def test_dates_and_top_categories() -> None:
    posts = [
        _post(1, "<p>a</p>", date="2026-03-02T23:30:00", categories=(1, 2)),
        _post(2, "<p>a</p>", date="2025-12-31T01:00:00", categories=(1,)),
        _post(3, "<p>a</p>", date="2026-11-01T08:00:00", categories=(9,)),
    ]
    stats = corpus_stats(posts, {1: "Musique", 2: "Cinéma"})
    assert (stats.posts, stats.first_date, stats.last_date) == (3, "2025-12-31", "2026-11-01")
    assert stats.top_categories == (("Musique", 2), ("#9", 1), ("Cinéma", 1))


# --- files and CLI ---


def _paths(tmp_path: Path) -> CorpusPaths:
    return CorpusPaths(dataset_dir=tmp_path / "datasets", data_dir=tmp_path / "data")


def test_no_local_posts_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(CorpusPaths, "default", lambda: paths)
    with pytest.raises(SystemExit, match="No local post"):
        corpus_main.main(["stats"])


def test_cli_prints_counters_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _paths(tmp_path)
    secret_title = "&laquo;Titre secret&raquo;"
    for post_id, content in ((1, ACCOUNTS_HTML), (2, "<p>Texte secret " + _quote(70) + "</p>")):
        write_json_atomic(paths.post_file(post_id), _record(post_id, content, title=secret_title))
    write_json_atomic(paths.categories, [{"id": 1, "name": "Musique &amp; Scène"}])
    csv_path = tmp_path / "following.csv"
    csv_path.write_text("username\nartist_one\n", encoding="utf-8")
    monkeypatch.setattr(CorpusPaths, "default", lambda: paths)

    corpus_main.main(["stats", "--following", str(csv_path)])

    out = capsys.readouterr().out
    assert "Posts: 2 (date_gmt 2026-10-01 to 2026-10-01)" in out
    assert "Titles with quotation marks: 2" in out
    assert "Musique & Scène 2" in out
    assert "Followed list of 1 accounts: 1 cited accounts followed" in out
    for leaked in ("secret", "artist_one", "second.artist", "aaaa"):
        assert leaked not in out.lower()
    assert json.loads(paths.post_file(1).read_text(encoding="utf-8"))["id"] == 1
