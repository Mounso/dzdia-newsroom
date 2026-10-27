import pytest

from evals.baseline_v1.sample import FeedConfig, SamplingConfig, build_sample, parse_feed

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>TSA</title>
<item><title>A</title><link>https://www.tsa-algerie.com/article-a/</link>
<guid isPermaLink="false">https://www.tsa-algerie.com/?p=101</guid></item>
<item><title>B</title><link>https://www.tsa-algerie.com/article-b/</link>
<guid isPermaLink="false">https://www.tsa-algerie.com/?p=102</guid></item>
<item><title>C</title><link>https://www.tsa-algerie.com/article-c/</link>
<guid isPermaLink="false">https://www.tsa-algerie.com/?p=103</guid></item>
</channel></rss>"""


def _config(n_opaque: int = 2, per_feed: int = 3) -> SamplingConfig:
    return SamplingConfig(
        feeds=[FeedConfig("tsa", "https://www.tsa-algerie.com/feed/")],
        per_feed=per_feed,
        n_opaque=n_opaque,
        opaque_feed="tsa",
        seed=1,
    )


def test_parse_feed_keeps_link_and_guid() -> None:
    entries = parse_feed(RSS)
    assert entries[0].link == "https://www.tsa-algerie.com/article-a/"
    assert entries[0].guid == "https://www.tsa-algerie.com/?p=101"


def test_build_sample_adds_seeded_opaque_duplicates() -> None:
    items = build_sample({"tsa": parse_feed(RSS)}, _config())
    originals = [i for i in items if not i.is_opaque]
    opaques = [i for i in items if i.is_opaque]

    assert [i.item_id for i in originals] == ["tsa-00", "tsa-01", "tsa-02"]
    assert len(opaques) == 2
    for opaque in opaques:
        assert opaque.item_id == f"{opaque.snapshot_id}-opaque"
        assert "?p=" in opaque.url
    # Same seed, same choice.
    assert build_sample({"tsa": parse_feed(RSS)}, _config()) == items


def test_build_sample_fails_when_feed_is_too_short() -> None:
    with pytest.raises(ValueError, match="need 5"):
        build_sample({"tsa": parse_feed(RSS)}, _config(per_feed=5))
