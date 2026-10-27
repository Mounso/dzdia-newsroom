"""Build the frozen URL sample from RSS feeds (most recent entries + opaque-URL duplicates)."""

import random
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import feedparser
import httpx

from evals.baseline_v1.schemas import SampleItem

USER_AGENT = "Mozilla/5.0 (compatible; DzdiaNewsroomEval/0.1)"
_OPAQUE_PERMALINK = re.compile(r"[?&]p=\d+$")


class FeedUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str


@dataclass(frozen=True)
class SamplingConfig:
    feeds: list[FeedConfig]
    per_feed: int
    n_opaque: int
    opaque_feed: str
    seed: int


@dataclass(frozen=True)
class FeedEntry:
    link: str
    title: str
    published: str | None
    guid: str | None


def load_sampling_config(path: Path) -> SamplingConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return SamplingConfig(
        feeds=[FeedConfig(name=f["name"], url=f["url"]) for f in raw["feeds"]],
        per_feed=raw["per_feed"],
        n_opaque=raw["n_opaque"],
        opaque_feed=raw["opaque_feed"],
        seed=raw["seed"],
    )


def parse_feed(content: bytes) -> list[FeedEntry]:
    parsed = feedparser.parse(content)
    return [
        FeedEntry(
            link=entry.get("link", ""),
            title=entry.get("title", ""),
            published=entry.get("published"),
            guid=entry.get("id"),
        )
        for entry in parsed.entries
        if entry.get("link")
    ]


async def fetch_feeds(
    config: SamplingConfig, client: httpx.AsyncClient
) -> dict[str, list[FeedEntry]]:
    entries: dict[str, list[FeedEntry]] = {}
    for feed in config.feeds:
        try:
            response = await client.get(feed.url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FeedUnavailableError(
                f"Feed '{feed.name}' unreachable ({feed.url}): {exc!r}"
            ) from exc
        entries[feed.name] = parse_feed(response.content)
    return entries


def build_sample(
    entries_by_feed: dict[str, list[FeedEntry]], config: SamplingConfig
) -> list[SampleItem]:
    """Take the `per_feed` most recent entries per feed, then add seeded opaque duplicates."""
    items: list[SampleItem] = []
    opaque_candidates: list[tuple[SampleItem, str]] = []

    for feed in config.feeds:
        entries = entries_by_feed[feed.name][: config.per_feed]
        if len(entries) < config.per_feed:
            raise ValueError(
                f"Feed '{feed.name}' has {len(entries)} entries, need {config.per_feed}"
            )
        for index, entry in enumerate(entries):
            item_id = f"{feed.name}-{index:02d}"
            item = SampleItem(
                item_id=item_id,
                source=feed.name,
                url=entry.link,
                title=entry.title,
                published=entry.published,
                snapshot_id=item_id,
            )
            items.append(item)
            if (
                feed.name == config.opaque_feed
                and entry.guid
                and _OPAQUE_PERMALINK.search(entry.guid)
            ):
                opaque_candidates.append((item, entry.guid))

    if len(opaque_candidates) < config.n_opaque:
        raise ValueError(
            f"Only {len(opaque_candidates)} opaque permalinks in '{config.opaque_feed}', "
            f"need {config.n_opaque}"
        )
    chosen = random.Random(config.seed).sample(opaque_candidates, config.n_opaque)
    for original, opaque_url in sorted(chosen, key=lambda pair: pair[0].item_id):
        items.append(
            original.model_copy(
                update={
                    "item_id": f"{original.item_id}-opaque",
                    "url": opaque_url,
                    "is_opaque": True,
                }
            )
        )
    return items
