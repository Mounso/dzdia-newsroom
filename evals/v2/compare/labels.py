"""The blind sheet and the labels of the comparison (ADR-0013, points 4, 5 and 7; ADR-0010,
points 6 to 8): `export-labels`, `import-labels` and `check-labels`.

- The sheet population is every element of the two new arms plus the `page_text / article`
  elements of the comparison items that have no label in the baseline. Each row carries an
  opaque key, a seeded hash of its element id: the arm, the run id, the element id and the
  matcher's verdict never appear in the sheet. One function maps keys to elements over the whole
  population; the three steps use it, so `keys.jsonl` is only an audit copy.
- The comparison's `labels.jsonl` is append-only and read with the latest label per element.
  A label keeps from its note only the speaker marker `locuteur`: any other note text stays in
  the local sheet.
- The steps print counts and keys only, never an arm, an element id, a value or a label.
"""

import csv
import hashlib
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from evals.baseline_v1.jsonl import append_jsonl, latest_by, read_jsonl, write_jsonl
from evals.baseline_v1.labeling import SPEAKER_MARKER, committed_note, parse_label
from evals.baseline_v1.matcher import normalize
from evals.baseline_v1.schemas import ElementRecord, HumanLabel, Label, SampleItem
from evals.v2.compare import CompareError
from evals.v2.compare.extract import latest_runs, pending_extractions
from evals.v2.compare.paths import ComparePaths
from evals.v2.compare.run import COMPARE_CONDITIONS, compare_run_id, comparison_items

# Committed with the code (ADR-0013, point 4): the keys can always be computed again.
SEED = "v2-compare-1"
KEY_LENGTH = 10  # hex characters of the hash in a key
# A letter first: a spreadsheet reads "0123456789" or "12e345" as a number and rewrites it on save.
KEY_PREFIX = "k"
COLUMNS = ("key", "item_id", "type", "value", "context", "label", "note")

# --- population and keys (pure) --------------------------------------------------------------


def key_from_hexdigest(hexdigest: str) -> str:
    """The key for a SHA-256 hex digest: the prefix and its first 10 hex characters."""
    return f"{KEY_PREFIX}{hexdigest[:KEY_LENGTH]}"


def sheet_key(element_id: str) -> str:
    """The prefix and the first 10 hex characters of SHA-256 of the seed and the element id."""
    return key_from_hexdigest(hashlib.sha256(f"{SEED}\x1f{element_id}".encode()).hexdigest())


def sheet_population(
    compare_elements: Iterable[ElementRecord],
    baseline_elements: Iterable[ElementRecord],
    baseline_labeled: Collection[str],
    item_ids: Collection[str],
) -> list[ElementRecord]:
    """Every element of the new arms, plus the baseline's `page_text / article` elements of
    `item_ids` that have no baseline label (ADR-0010, point 6)."""
    earlier = [
        e
        for e in baseline_elements
        if e.condition == "page_text"
        and e.task == "article"
        and e.item_id in item_ids
        and e.element_id not in baseline_labeled
    ]
    return [*compare_elements, *earlier]


def key_index(population: Iterable[ElementRecord]) -> dict[str, ElementRecord]:
    """Key -> element over the whole population. Two elements with the same key raise."""
    index: dict[str, ElementRecord] = {}
    for e in population:
        key = sheet_key(e.element_id)
        if key in index:
            raise CompareError(
                f"Key collision on {key}: change the seed before the first export."
                if index[key].element_id != e.element_id
                else f"Key {key}: the same element is twice in the population."
            )
        index[key] = e
    return index


def ordered_keys(
    keys: Iterable[str], index: Mapping[str, ElementRecord], item_order: Sequence[str]
) -> list[str]:
    """Items in sample order; inside an item, keys in sorted order (a fixed shuffle)."""
    position = {item_id: n for n, item_id in enumerate(item_order)}
    keys = list(keys)
    outside = sorted({index[k].item_id for k in keys} - position.keys())
    if outside:
        raise CompareError(f"Elements of items outside the comparison: {outside}")
    return sorted(keys, key=lambda k: (position[index[k].item_id], k))


@dataclass(frozen=True)
class Population:
    items: list[SampleItem]  # the comparison items, in sample order
    index: dict[str, ElementRecord]  # key -> element

    @property
    def item_order(self) -> list[str]:
        return [i.item_id for i in self.items]


def load_population(paths: ComparePaths) -> Population:
    items = comparison_items(paths)
    baseline_labeled = {lab.element_id for lab in read_jsonl(paths.baseline.labels, HumanLabel)}
    population = sheet_population(
        read_jsonl(paths.elements, ElementRecord),
        read_jsonl(paths.baseline.elements, ElementRecord),
        baseline_labeled,
        {i.item_id for i in items},
    )
    return Population(items, key_index(population))


def latest_labels(paths: ComparePaths) -> dict[str, HumanLabel]:
    """The latest label of each element in the comparison's append-only labels file."""
    return latest_by(read_jsonl(paths.labels, HumanLabel), lambda lab: lab.element_id)


# --- the sheet -------------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetRow:
    """What the labeler can change in a row, as read from the sheet."""

    key: str
    label: str  # raw cell; blank: pending
    note: str  # reduced to the speaker marker or "" (`committed_note`)

    @property
    def filled(self) -> bool:
        return bool(self.label.strip())


def write_sheet(path: Path, keys: Iterable[str], index: Mapping[str, ElementRecord]) -> int:
    """The baseline's CSV format (';', UTF-8 with BOM), with the blind columns only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        for key in keys:
            e = index[key]
            writer.writerow(
                {
                    "key": key,
                    "item_id": e.item_id,
                    "type": e.type,
                    "value": e.value,
                    "context": e.context,
                    "label": "",
                    "note": "",
                }
            )
            count += 1
    return count


def read_sheet(path: Path) -> list[SheetRow]:
    if not path.exists():
        raise CompareError(f"No sheet at {path}: run 'export-labels' first.")
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            missing = [c for c in ("key", "label", "note") if c not in (reader.fieldnames or [])]
            if missing:
                raise CompareError(f"{path} has no column {', '.join(missing)}.")
            return [
                SheetRow(
                    key=(row.get("key") or "").strip(),
                    label=row.get("label") or "",
                    note=committed_note(row.get("note") or ""),
                )
                for row in reader
            ]
    except UnicodeDecodeError as exc:
        raise CompareError(
            f"{path} is not UTF-8: save it as \"CSV UTF-8\" with ';' as separator and run the "
            "step again. Nothing was imported or overwritten."
        ) from exc


def _parsed(row: SheetRow) -> Label | None:
    try:
        return parse_label(row.label)
    except ValueError:
        return None


def unimported_rows(
    rows: Iterable[SheetRow],
    index: Mapping[str, ElementRecord],
    labels: Mapping[str, HumanLabel],
) -> list[SheetRow]:
    """Filled rows whose label and note are not those of the latest imported label of their
    key (an unknown key or an invalid label counts as not imported)."""
    pending: list[SheetRow] = []
    for row in rows:
        if not row.filled:
            continue
        element = index.get(row.key)
        latest = labels.get(element.element_id) if element else None
        if latest is None or latest.label != _parsed(row) or latest.note != row.note:
            pending.append(row)
    return pending


# --- export-labels ---------------------------------------------------------------------------


class KeyRecord(BaseModel):
    key: str
    element_id: str


class ExportRecord(BaseModel):
    exported_at: datetime
    seed: str
    n_rows: int
    rows_per_item: dict[str, int]  # all arms together, every comparison item in sample order
    guide_sha256: str


def guide_sha256(path: Path) -> str:
    """SHA-256 of the guide's UTF-8 text with CRLF read as LF: the hash does not depend on the
    checkout's line endings."""
    if not path.exists():
        raise CompareError(f"Missing labeling guide {path}.")
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_export_ready(paths: ComparePaths, items: Iterable[SampleItem]) -> None:
    """Refuses unless every planned run has a result, every extractable run a successful
    extraction and every element of the new arms a match status. Counts only in messages."""
    runs = latest_runs(paths)
    planned = [compare_run_id(i.item_id, c) for i in items for c in COMPARE_CONDITIONS]
    unrun = sum(run_id not in runs or runs[run_id].error is not None for run_id in planned)
    if unrun:
        raise CompareError(
            f"{unrun} planned runs have no successful record: run the 'run' step first."
        )
    unextracted = len(pending_extractions(paths))
    if unextracted:
        raise CompareError(
            f"{unextracted} runs have no successful extraction: run the 'extract' step first."
        )
    unmatched = sum(e.match_status is None for e in read_jsonl(paths.elements, ElementRecord))
    if unmatched:
        raise CompareError(f"{unmatched} elements are not matched: run the 'match' step first.")


def export_labels(paths: ComparePaths) -> ExportRecord:
    """The `export-labels` step. Every check runs before anything is written."""
    population = load_population(paths)
    check_export_ready(paths, population.items)
    guide_hash = guide_sha256(paths.labeling_guide)
    labels = latest_labels(paths)
    if paths.label_sheet.exists():
        unimported = unimported_rows(read_sheet(paths.label_sheet), population.index, labels)
        if unimported:
            raise CompareError(
                f"{paths.label_sheet} holds {len(unimported)} labels not imported yet: run "
                "'import-labels' first."
            )

    index = population.index
    all_keys = ordered_keys(index, index, population.item_order)
    keys = [k for k in all_keys if index[k].element_id not in labels]
    n_rows = write_sheet(paths.label_sheet, keys, index)
    write_jsonl(paths.key_map, [KeyRecord(key=k, element_id=index[k].element_id) for k in all_keys])
    per_item = {item_id: 0 for item_id in population.item_order}
    for k in keys:
        per_item[index[k].item_id] += 1
    record = ExportRecord(
        exported_at=datetime.now(UTC),
        seed=SEED,
        n_rows=n_rows,
        rows_per_item=per_item,
        guide_sha256=guide_hash,
    )
    append_jsonl(paths.export_log, [record])
    return record


# --- import-labels ---------------------------------------------------------------------------


def parse_sheet_labels(
    rows: Iterable[SheetRow], index: Mapping[str, ElementRecord]
) -> list[HumanLabel]:
    """The filled rows as labels. Every row is checked first: an unknown key, an invalid label
    or a key given twice raises, naming the keys only."""
    errors: list[str] = []
    labels: list[HumanLabel] = []
    seen: set[str] = set()
    for row in rows:
        if not row.filled:
            continue
        if row.key in seen:
            errors.append(f"key {row.key} is filled on two rows")
            continue
        seen.add(row.key)
        element = index.get(row.key)
        if element is None:
            errors.append(f"unknown key {row.key}")
            continue
        label = _parsed(row)
        if label is None:
            errors.append(f"invalid label on key {row.key} (use S, C or N)")
            continue
        labels.append(HumanLabel(element_id=element.element_id, label=label, note=row.note))
    if errors:
        raise CompareError("Nothing imported: " + "; ".join(errors) + ".")
    return labels


@dataclass(frozen=True)
class ImportSummary:
    appended: int
    changed: int  # appended labels whose value differs from the previous label of the element
    labeled: int  # elements of the population with a label
    pending: int  # elements of the population without one


def new_labels(
    parsed: Iterable[HumanLabel], latest: Mapping[str, HumanLabel]
) -> tuple[list[HumanLabel], int]:
    """The labels to append (new, or label or note changed) and how many changed value."""
    to_append: list[HumanLabel] = []
    changed = 0
    for label in parsed:
        previous = latest.get(label.element_id)
        if previous is not None and (previous.label, previous.note) == (label.label, label.note):
            continue
        to_append.append(label)
        changed += previous is not None and previous.label != label.label
    return to_append, changed


def import_labels(paths: ComparePaths) -> ImportSummary:
    """The `import-labels` step: validates the whole sheet, then appends. A blank cell is
    pending and never removes an imported label."""
    population = load_population(paths)
    parsed = parse_sheet_labels(read_sheet(paths.label_sheet), population.index)
    latest = latest_labels(paths)
    to_append, changed = new_labels(parsed, latest)
    if to_append:
        append_jsonl(paths.labels, to_append)
    latest.update({lab.element_id: lab for lab in to_append})
    labeled = sum(e.element_id in latest for e in population.index.values())
    return ImportSummary(
        appended=len(to_append),
        changed=changed,
        labeled=labeled,
        pending=len(population.index) - labeled,
    )


# --- check-labels ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LabeledRow:
    key: str
    element: ElementRecord
    label: HumanLabel


@dataclass(frozen=True)
class CheckResult:
    quote_supported_not_found: list[LabeledRow]  # check 1
    inconsistent_values: list[LabeledRow]  # check 2, group after group
    locuteur_notes: list[LabeledRow]  # check 3


def labeled_rows(
    index: Mapping[str, ElementRecord],
    labels: Mapping[str, HumanLabel],
    item_order: Sequence[str],
) -> list[LabeledRow]:
    """The population elements with a comparison label, in sheet order."""
    keys = [k for k, e in index.items() if e.element_id in labels]
    return [
        LabeledRow(k, index[k], labels[index[k].element_id])
        for k in ordered_keys(keys, index, item_order)
    ]


def run_checks(rows: Sequence[LabeledRow]) -> CheckResult:
    """The three checks of ADR-0013, point 7. `rows` in sheet order; so are the results."""
    quotes = [
        r
        for r in rows
        if r.element.type == "quote"
        and r.label.label == "SUPPORTED"
        and r.element.match_status == "not_found"
    ]
    groups: defaultdict[tuple[str, str, str], list[LabeledRow]] = defaultdict(list)
    for r in rows:
        groups[(r.element.item_id, r.element.type, normalize(r.element.value))].append(r)
    inconsistent = [
        r for group in groups.values() if len({r.label.label for r in group}) > 1 for r in group
    ]
    locuteur = [
        r
        for r in rows
        if SPEAKER_MARKER in r.label.note.casefold()
        and not (r.element.type == "quote" and r.label.label == "CONTRADICTED")
    ]
    return CheckResult(quotes, inconsistent, locuteur)


class CheckRecord(BaseModel):
    checked_at: datetime
    n_labels: int
    quote_supported_not_found: int
    inconsistent_values: int
    locuteur_notes: int


def check_labels(paths: ComparePaths) -> tuple[CheckResult, CheckRecord]:
    """The `check-labels` step: the checks on the latest comparison labels of the population,
    and one line of counts appended to the check log."""
    population = load_population(paths)
    rows = labeled_rows(population.index, latest_labels(paths), population.item_order)
    result = run_checks(rows)
    record = CheckRecord(
        checked_at=datetime.now(UTC),
        n_labels=len(rows),
        quote_supported_not_found=len(result.quote_supported_not_found),
        inconsistent_values=len(result.inconsistent_values),
        locuteur_notes=len(result.locuteur_notes),
    )
    append_jsonl(paths.check_log, [record])
    return result, record


_CHECK_TITLES = (
    "Check 1, quotes labeled SUPPORTED that the matcher did not find",
    "Check 2, rows of one article with the same type and normalized value but different labels",
    "Check 3, 'locuteur' in the note of a row that is not a quote labeled CONTRADICTED",
)


def check_lines(result: CheckResult, record: CheckRecord) -> list[str]:
    """Per check, its count and its flagged rows as `item_id key`: never an arm, an element id,
    a value or a label."""
    lines = [f"Checked {record.n_labels} labels."]
    flagged = (result.quote_supported_not_found, result.inconsistent_values, result.locuteur_notes)
    for title, rows in zip(_CHECK_TITLES, flagged, strict=True):
        lines.append(f"{title}: {len(rows)}")
        lines.extend(f"  {r.element.item_id} {r.key}" for r in rows)
    return lines
