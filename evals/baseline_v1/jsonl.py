"""Minimal JSONL persistence. Append-only files are deduplicated on read (last record wins)."""

from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import BaseModel


def read_jsonl[M: BaseModel](path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


def append_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")


def latest_by[M: BaseModel](records: Iterable[M], key: Callable[[M], str]) -> dict[str, M]:
    """Index records by key, keeping the last occurrence (later retries override failures)."""
    return {key(r): r for r in records}
