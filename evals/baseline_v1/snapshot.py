"""Freeze each source page (raw HTML, extracted text, hashes): v1 and v2 see the same text.

The extracted text is reviewed by hand afterwards (`review-done`), which re-hashes it: the reviewed
text is the labeling reference.
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import trafilatura

from evals.baseline_v1.sample import USER_AGENT
from evals.baseline_v1.schemas import SampleItem, SnapshotMeta


class ExtractionError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# In-body links to other articles ("🟢 À LIRE AUSSI : <other headline>"): their facts are not part
# of the article and would make unrelated elements look "found".
_RELATED_LINK_LINE = re.compile(r"^\W*[AÀ] LIRE AUSSI\b.*$", re.IGNORECASE | re.MULTILINE)


def clean_text(text: str) -> str:
    text = _RELATED_LINK_LINE.sub("", text)
    return "\n".join(line for line in text.splitlines() if line.strip())


def extract_main_text(html: str) -> str:
    text = trafilatura.extract(
        html, include_comments=False, include_tables=True, favor_precision=True
    )
    if not text or not text.strip():
        raise ExtractionError("trafilatura found no readable article body")
    return clean_text(str(text))


def reextract(meta: SnapshotMeta, snapshot_dir: Path) -> SnapshotMeta:
    """Rebuild the text from the stored HTML (no refetch), e.g. after improving the cleaning."""
    html = html_path(snapshot_dir, meta.snapshot_id).read_text(encoding="utf-8")
    text = extract_main_text(html)
    text_path(snapshot_dir, meta.snapshot_id).write_text(text, encoding="utf-8")
    return meta.model_copy(
        update={"text_sha256": sha256_text(text), "n_chars": len(text), "reviewed": False}
    )


def html_path(snapshot_dir: Path, snapshot_id: str) -> Path:
    return snapshot_dir / f"{snapshot_id}.html"


def text_path(snapshot_dir: Path, snapshot_id: str) -> Path:
    return snapshot_dir / f"{snapshot_id}.txt"


def load_snapshot_text(snapshot_dir: Path, snapshot_id: str) -> str:
    return text_path(snapshot_dir, snapshot_id).read_text(encoding="utf-8")


async def snapshot_item(
    client: httpx.AsyncClient, item: SampleItem, snapshot_dir: Path
) -> SnapshotMeta:
    response = await client.get(item.url, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    html = response.text
    text = extract_main_text(html)

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    html_path(snapshot_dir, item.item_id).write_text(html, encoding="utf-8")
    text_path(snapshot_dir, item.item_id).write_text(text, encoding="utf-8")
    return SnapshotMeta(
        snapshot_id=item.item_id,
        url=item.url,
        final_url=str(response.url),
        fetched_at=datetime.now(UTC),
        html_sha256=sha256_text(html),
        text_sha256=sha256_text(text),
        n_chars=len(text),
    )


async def resolve_final_url(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return str(response.url)


def mark_reviewed(meta: SnapshotMeta, snapshot_dir: Path) -> SnapshotMeta:
    """Record the hash of the hand-reviewed text, which becomes the labeling reference."""
    text = load_snapshot_text(snapshot_dir, meta.snapshot_id)
    return meta.model_copy(
        update={"text_sha256": sha256_text(text), "n_chars": len(text), "reviewed": True}
    )
