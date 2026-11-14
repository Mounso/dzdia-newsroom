"""Records of the Dzdia corpus: the fields read from an API post, the manifest line, the fetch
log line, and the local JSON files (ADR-0015, points 4 and 5)."""

import html
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.dzdia_corpus import CorpusError
from evals.dzdia_corpus.paths import CorpusPaths

FetchMode = Literal["first", "incremental", "full"]


class Rendered(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    rendered: str


class WpPost(BaseModel):
    """The fields of a WordPress REST post record this package reads. The record itself is
    stored as returned; this view only validates it."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int
    link: str
    date_gmt: str
    modified_gmt: str
    title: Rendered
    content: Rendered
    categories: list[int]


class WpCategory(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int
    name: str


class ManifestLine(BaseModel):
    """One committed line per post: no title, no text, no author, no tags."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    url: str
    published_gmt: str
    modified_gmt: str
    categories: list[str]
    content_sha256: str
    content_chars: int


class FetchRun(BaseModel):
    """One committed line per successful `fetch` run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fetched_at: datetime
    mode: FetchMode
    requests: int
    posts_listed: int
    new: int
    changed: int
    unchanged: int
    total_in_manifest: int


def parse_post(record: Any) -> WpPost:
    try:
        return WpPost.model_validate(record)
    except ValidationError as exc:
        raise CorpusError(f"Unexpected post record: {exc.error_count()} invalid fields") from exc


def parse_categories(records: Any) -> list[WpCategory]:
    if not isinstance(records, list):
        raise CorpusError("Unexpected categories response: not a JSON list")
    try:
        return [WpCategory.model_validate(r) for r in records]
    except ValidationError as exc:
        n = exc.error_count()
        raise CorpusError(f"Unexpected category record: {n} invalid fields") from exc


def category_names(categories: Iterable[WpCategory]) -> dict[int, str]:
    """Category id -> name, HTML-unescaped (the API returns `&amp;` for `&`)."""
    return {c.id: html.unescape(c.name) for c in categories}


def names_of(ids: Sequence[int], names: Mapping[int, str]) -> list[str]:
    """Sorted category names of a post; an id missing from `names` is kept as `#<id>`."""
    return sorted(names.get(i, f"#{i}") for i in ids)


def write_json_atomic(path: Path, data: Any) -> None:
    """UTF-8 JSON, non-ASCII kept, sorted keys, two-space indent, `\\n` newlines; written to a
    temporary file next to `path`, then moved over it, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"Cannot read {path.name}: {type(exc).__name__}") from exc


def post_ids(paths: CorpusPaths) -> list[int]:
    """Ids of the local post files, ascending (files whose stem is not an integer are skipped)."""
    if not paths.posts_dir.is_dir():
        return []
    return sorted(int(p.stem) for p in paths.posts_dir.glob("*.json") if p.stem.isdigit())


def read_categories(paths: CorpusPaths) -> dict[int, str]:
    """The stored category names, or an empty mapping when `categories.json` is missing."""
    if not paths.categories.exists():
        return {}
    return category_names(parse_categories(read_json(paths.categories)))
