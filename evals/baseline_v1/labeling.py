"""Human labeling round-trip through a CSV sheet (Excel-friendly: ';' separator, UTF-8 BOM).

Selected for labeling: every `not_found` element + a stable ~20 % audit sample of `found` ones.
The audit sample is chosen by hashing the element_id, so it does not change when elements are
added or the pipeline is re-run.
"""

import csv
import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import get_args

from evals.baseline_v1.schemas import ElementRecord, HumanLabel, Label

AUDIT_RATE_PERCENT = 20
_SHORTCUTS: dict[str, Label] = {"S": "SUPPORTED", "C": "CONTRADICTED", "N": "NOT_IN_SOURCE"}
_COLUMNS = [
    "element_id", "item_id", "condition", "task", "type", "value", "context",
    "match_status", "match_score", "label", "note",
]  # fmt: skip


def in_audit_sample(element_id: str, rate_percent: int = AUDIT_RATE_PERCENT) -> bool:
    bucket = int(hashlib.sha256(element_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return bucket < rate_percent


def select_for_labeling(
    elements: Iterable[ElementRecord], already_labeled: set[str]
) -> list[ElementRecord]:
    selected = [
        e
        for e in elements
        if e.element_id not in already_labeled
        and (e.match_status == "not_found" or in_audit_sample(e.element_id))
    ]
    return sorted(selected, key=lambda e: (e.item_id, e.run_id, e.element_id))


def write_label_sheet(path: Path, elements: Iterable[ElementRecord]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS, delimiter=";")
        writer.writeheader()
        for e in elements:
            writer.writerow(
                {
                    **e.model_dump(include=set(_COLUMNS)),
                    "match_score": "" if e.match_score is None else f"{e.match_score:.0f}",
                    "label": "",
                    "note": "",
                }
            )
            count += 1
    return count


def parse_label(raw: str) -> Label:
    value = raw.strip().upper()
    if value in _SHORTCUTS:
        return _SHORTCUTS[value]
    if value in get_args(Label):
        return value  # type: ignore[return-value]  # membership checked just above
    raise ValueError(f"Unknown label '{raw}': use S, C, N or {', '.join(get_args(Label))}")


def read_label_sheet(path: Path) -> list[HumanLabel]:
    """Return the rows that have a label; rows left blank are still pending."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    return [
        HumanLabel(element_id=row["element_id"], label=parse_label(row["label"]), note=row["note"])
        for row in rows
        if row.get("label", "").strip()
    ]
