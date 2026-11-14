"""The blind sheet of the v2 comparison (ADR-0013, points 4, 5 and 7): population, keys, export,
import and the three checks. Files under `tmp_path` only; nothing printed per row but keys."""

import csv
import hashlib
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evals.baseline_v1.extract import element_id
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.schemas import (
    ElementRecord,
    ElementType,
    ExtractionRecord,
    HumanLabel,
    Label,
)
from evals.v2.compare import CompareError, labels
from evals.v2.compare import __main__ as compare_main
from evals.v2.compare.labels import (
    COLUMNS,
    SEED,
    CheckRecord,
    ExportRecord,
    KeyRecord,
    check_labels,
    export_labels,
    import_labels,
    key_from_hexdigest,
    key_index,
    load_population,
    sheet_key,
    sheet_population,
)
from evals.v2.compare.paths import ComparePaths
from tests.evals.test_v2_compare_run import NOW, _paths, _record

# Sample order (tsa-02, its opaque duplicate, tsa-00) differs from alphabetical order.
ITEMS = ("tsa-02", "tsa-00")
GUIDE = "# Guide\nRègle 1.\n"


def _element(
    item_id: str,
    condition: str,
    value: str,
    type_: ElementType = "person",
    *,
    status: str | None = "found",
    task: str = "article",
) -> ElementRecord:
    run_id = f"{item_id}:{condition}:{task}:0"
    return ElementRecord.model_validate(
        {
            "element_id": element_id(run_id, type_, value),
            "run_id": run_id,
            "item_id": item_id,
            "condition": condition,
            "task": task,
            "type": type_,
            "value": value,
            "context": f"Contexte {value}.",
            "match_status": status,
            "match_score": None if status is None else 100.0 if status == "found" else 0.0,
        }
    )


V2_NAME = _element("tsa-00", "v2", "Karim Ziani")
PLAIN_NAME = _element("tsa-00", "plain_prompt", "Karim Ziani")
PLAIN_QUOTE = _element("tsa-00", "plain_prompt", "Nous reviendrons", "quote", status="not_found")
V2_DATE = _element("tsa-02", "v2", "3 mai", "date")
NEW_ARMS = [V2_NAME, PLAIN_NAME, PLAIN_QUOTE, V2_DATE]

PAGE_TEXT_PLACE = _element("tsa-00", "page_text", "Oran", "place")
PAGE_TEXT_NAME = _element("tsa-02", "page_text", "Ali Benali")
PAGE_TEXT_LABELED = _element("tsa-02", "page_text", "12", "number")
OUT_OF_SCOPE = [
    PAGE_TEXT_LABELED,  # labeled in the baseline
    _element("tsa-00", "url_only", "Karim Ziani"),
    _element("tsa-00", "page_text", "Oran", "place", task="summary"),
    _element("tsa-02-opaque", "page_text", "Sétif", "place"),  # opaque item
    _element("tsa-09", "page_text", "Tlemcen", "place"),  # not in the sample
]
BASELINE = [PAGE_TEXT_PLACE, PAGE_TEXT_NAME, *OUT_OF_SCOPE]
POPULATION = [*NEW_ARMS, PAGE_TEXT_PLACE, PAGE_TEXT_NAME]
BASELINE_LABELS = [HumanLabel(element_id=PAGE_TEXT_LABELED.element_id, label="SUPPORTED")]


def _setup(
    tmp_path: Path,
    new_arms: list[ElementRecord] | None = None,
    runs: list[Any] | None = None,
) -> ComparePaths:
    """Every planned run done (no text: nothing to extract), the elements matched, a guide."""
    paths = replace(_paths(tmp_path, ITEMS), labeling_guide=tmp_path / "guide.md")
    paths.labeling_guide.write_bytes(GUIDE.encode("utf-8"))
    if runs is None:
        runs = [_record(i, c) for i in ITEMS for c in ("v2", "plain_prompt")]
    write_jsonl(paths.runs, runs)
    write_jsonl(paths.elements, NEW_ARMS if new_arms is None else new_arms)
    write_jsonl(paths.baseline.elements, BASELINE)
    write_jsonl(paths.baseline.labels, BASELINE_LABELS)
    return paths


def _key(e: ElementRecord) -> str:
    return sheet_key(e.element_id)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _fill(path: Path, cells: dict[str, tuple[str, str]], extra: Sequence[str] = ()) -> None:
    """Sets (label, note) on the rows of the given keys; `extra` adds rows with unknown keys."""
    rows = _rows(path)
    for row in rows:
        if row["key"] in cells:
            row["label"], row["note"] = cells[row["key"]]
    rows += [dict.fromkeys(COLUMNS, "") | {"key": k, "label": "S"} for k in extra]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _labels(paths: ComparePaths) -> list[HumanLabel]:
    return read_jsonl(paths.labels, HumanLabel)


# --- population and keys ---------------------------------------------------------------------


def test_the_population_is_the_new_arms_and_the_unlabeled_page_text_elements(
    tmp_path: Path,
) -> None:
    population = sheet_population(
        NEW_ARMS, BASELINE, {PAGE_TEXT_LABELED.element_id}, {"tsa-00", "tsa-02"}
    )
    assert population == POPULATION

    loaded = load_population(_setup(tmp_path))
    assert [i.item_id for i in loaded.items] == ["tsa-02", "tsa-00"]  # opaque one excluded
    assert sorted(e.element_id for e in loaded.index.values()) == sorted(
        e.element_id for e in POPULATION
    )


def test_the_key_is_a_seeded_hash_of_the_element_id() -> None:
    digest = hashlib.sha256(f"v2-compare-1\x1f{V2_NAME.element_id}".encode()).hexdigest()
    expected = "k" + digest[:10]
    assert SEED == "v2-compare-1"
    assert sheet_key(V2_NAME.element_id) == expected
    assert key_index(POPULATION) == key_index(reversed(POPULATION))
    assert key_index(POPULATION)[expected] == V2_NAME
    assert len({sheet_key(e.element_id) for e in POPULATION}) == len(POPULATION)


def test_the_key_is_a_prefix_and_ten_hex_characters_never_a_number() -> None:
    assert all(re.fullmatch(r"k[0-9a-f]{10}", _key(e)) for e in POPULATION)
    # A spreadsheet would read these hash prefixes as numbers: 123, then 1.2e+56.
    for digest in ("0000000123" + "a" * 54, "12e5600000" + "b" * 54):
        key = key_from_hexdigest(digest)
        assert key == "k" + digest[:10]
        with pytest.raises(ValueError):
            float(key)


def test_a_key_collision_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(labels, "sheet_key", lambda element_id: "0" * 10)
    with pytest.raises(CompareError, match="Key collision on 0000000000"):
        key_index(POPULATION)


def test_a_collision_stops_the_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _setup(tmp_path)
    monkeypatch.setattr(labels, "sheet_key", lambda element_id: "0" * 10)
    with pytest.raises(CompareError, match="collision"):
        export_labels(paths)
    assert not paths.label_sheet.exists()


# --- export-labels ---------------------------------------------------------------------------


def test_the_sheet_is_blind_and_ordered_by_item_then_key(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    record = export_labels(paths)

    raw = paths.label_sheet.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[0] == "key;item_id;type;value;context;label;note"
    for hidden in ("v2", "plain_prompt", "page_text", "article", "found", "condition", "run"):
        assert hidden not in text
    assert all(e.element_id not in text for e in POPULATION)

    rows = _rows(paths.label_sheet)
    tsa02 = sorted(_key(e) for e in POPULATION if e.item_id == "tsa-02")
    tsa00 = sorted(_key(e) for e in POPULATION if e.item_id == "tsa-00")
    assert [r["key"] for r in rows] == tsa02 + tsa00
    by_key = {r["key"]: r for r in rows}
    assert by_key[_key(PLAIN_QUOTE)] == {
        "key": _key(PLAIN_QUOTE),
        "item_id": "tsa-00",
        "type": "quote",
        "value": "Nous reviendrons",
        "context": "Contexte Nous reviendrons.",
        "label": "",
        "note": "",
    }
    assert record.n_rows == 6
    assert record.rows_per_item == {"tsa-02": 2, "tsa-00": 4}

    keys = read_jsonl(paths.key_map, KeyRecord)
    assert {k.key: k.element_id for k in keys} == {_key(e): e.element_id for e in POPULATION}


def test_the_export_logs_seed_rows_and_guide_hash(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    paths.labeling_guide.write_bytes(GUIDE.replace("\n", "\r\n").encode("utf-8"))
    export_labels(paths)

    first, second = read_jsonl(paths.export_log, ExportRecord)
    assert first.seed == SEED and first.n_rows == 6
    assert first.rows_per_item == {"tsa-02": 2, "tsa-00": 4}
    assert first.guide_sha256 == hashlib.sha256(GUIDE.encode("utf-8")).hexdigest()
    assert second.guide_sha256 == first.guide_sha256  # CRLF checkout, same hash


def test_a_missing_guide_stops_the_export(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    paths.labeling_guide.unlink()
    with pytest.raises(CompareError, match="Missing labeling guide"):
        export_labels(paths)
    assert not paths.label_sheet.exists() and not paths.export_log.exists()


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ("missing_run", "1 planned runs have no successful record"),
        ("error_run", "1 planned runs have no successful record"),
        ("unextracted", "1 runs have no successful extraction"),
        ("extraction_error", "1 runs have no successful extraction"),
        ("unmatched", "1 elements are not matched"),
    ],
)
def test_the_export_preconditions(tmp_path: Path, change: str, match: str) -> None:
    runs = [_record(i, c) for i in ITEMS for c in ("v2", "plain_prompt")]
    new_arms = list(NEW_ARMS)
    if change == "missing_run":
        runs.pop()
    elif change == "error_run":
        runs.append(_record("tsa-00", "v2", error="TimeoutError()"))
    elif change in ("unextracted", "extraction_error"):
        runs.append(_record("tsa-00", "plain_prompt", output_text="Texte"))
    elif change == "unmatched":
        new_arms[0] = V2_NAME.model_copy(update={"match_status": None})
    paths = _setup(tmp_path, new_arms, runs)
    if change == "extraction_error":
        extraction = ExtractionRecord(
            run_id="tsa-00:plain_prompt:article:0",
            model="m",
            prompt_version="extract-v1",
            created_at=NOW,
            error="TimeoutError()",
        )
        write_jsonl(paths.extractions, [extraction])
    with pytest.raises(CompareError, match=match):
        export_labels(paths)
    assert not paths.label_sheet.exists() and not paths.export_log.exists()

    if change == "extraction_error":  # a successful extraction afterwards unblocks it
        write_jsonl(paths.extractions, [extraction.model_copy(update={"error": None})])
        assert export_labels(paths).n_rows == 6


def test_a_failed_v2_generation_does_not_block_the_export(tmp_path: Path) -> None:
    runs = [_record(i, c) for i in ITEMS for c in ("v2", "plain_prompt")]
    runs.append(_record("tsa-00", "v2", outcome="failed", rendered_text="Texte"))
    assert export_labels(_setup(tmp_path, runs=runs)).n_rows == 6


def test_the_export_refuses_to_overwrite_labels_not_imported(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    _fill(paths.label_sheet, {_key(V2_NAME): ("S", "")})
    with pytest.raises(CompareError, match="1 labels not imported yet: run 'import-labels'"):
        export_labels(paths)
    assert {r["key"]: r["label"] for r in _rows(paths.label_sheet)}[_key(V2_NAME)] == "S"

    import_labels(paths)
    _fill(paths.label_sheet, {_key(V2_NAME): ("S", "locuteur")})  # the note changed: not imported
    with pytest.raises(CompareError, match="import-labels"):
        export_labels(paths)
    _fill(paths.label_sheet, {_key(V2_NAME): ("supported", "relu")})  # same label, other spelling;
    # free text without the speaker marker is never imported, so it changes nothing
    record = export_labels(paths)

    assert record.n_rows == 5  # the labeled row is not written again
    assert _key(V2_NAME) not in {r["key"] for r in _rows(paths.label_sheet)}
    assert record.rows_per_item == {"tsa-02": 2, "tsa-00": 3}


# --- import-labels ---------------------------------------------------------------------------


def test_a_partial_import_appends_the_filled_rows_only(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    _fill(paths.label_sheet, {_key(V2_NAME): ("s", " "), _key(PLAIN_QUOTE): ("C", " locuteur ")})
    summary = import_labels(paths)

    assert (summary.appended, summary.changed, summary.labeled, summary.pending) == (2, 0, 2, 4)
    # Appended in sheet order: tsa-00 rows, by key.
    expected = [
        HumanLabel(element_id=V2_NAME.element_id, label="SUPPORTED", note=""),
        HumanLabel(element_id=PLAIN_QUOTE.element_id, label="CONTRADICTED", note="locuteur"),
    ]
    assert _labels(paths) == sorted(expected, key=lambda lab: sheet_key(lab.element_id))
    assert read_jsonl(paths.baseline.labels, HumanLabel) == BASELINE_LABELS  # never written


@pytest.mark.parametrize(
    ("cells", "extra", "match"),
    [
        ({"V2": ("S", ""), "QUOTE": ("X", "")}, [], "invalid label on key QUOTE"),
        ({"V2": ("S", "")}, ["kffffffffff"], "unknown key kffffffffff"),
    ],
)
def test_an_invalid_sheet_writes_nothing(
    tmp_path: Path, cells: dict[str, tuple[str, str]], extra: list[str], match: str
) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    keys = {"V2": _key(V2_NAME), "QUOTE": _key(PLAIN_QUOTE)}
    _fill(paths.label_sheet, {keys[k]: v for k, v in cells.items()}, extra)
    with pytest.raises(CompareError, match=match.replace("QUOTE", keys["QUOTE"])) as error:
        import_labels(paths)
    assert not paths.labels.exists()
    assert "Nothing imported" in str(error.value)
    assert V2_NAME.element_id not in str(error.value)


def test_import_is_append_only_and_counts_changes(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    _fill(paths.label_sheet, {_key(V2_NAME): ("S", ""), _key(V2_DATE): ("N", "")})
    import_labels(paths)

    unchanged = import_labels(paths)  # same sheet: nothing new
    assert (unchanged.appended, unchanged.changed) == (0, 0)

    _fill(paths.label_sheet, {_key(V2_NAME): ("C", ""), _key(V2_DATE): ("N", "Locuteur ?")})
    summary = import_labels(paths)
    assert (summary.appended, summary.changed, summary.labeled) == (2, 1, 2)
    # Appended in sheet order: the tsa-02 row first.
    assert [(lab.element_id, lab.label, lab.note) for lab in _labels(paths)] == [
        (V2_DATE.element_id, "NOT_IN_SOURCE", ""),
        (V2_NAME.element_id, "SUPPORTED", ""),
        (V2_DATE.element_id, "NOT_IN_SOURCE", "locuteur"),
        (V2_NAME.element_id, "CONTRADICTED", ""),
    ]

    # A blank cell never removes an imported label.
    _fill(paths.label_sheet, {_key(V2_NAME): ("", ""), _key(V2_DATE): ("", "")})
    blank = import_labels(paths)
    assert (blank.appended, blank.labeled, blank.pending) == (0, 2, 4)
    assert len(_labels(paths)) == 4


def test_an_imported_note_keeps_only_the_speaker_marker(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    _fill(
        paths.label_sheet,
        {
            _key(V2_NAME): ("S", "mauvais locuteur : X"),
            _key(PLAIN_QUOTE): ("C", "Mauvais LOCUTEUR : X"),
            _key(V2_DATE): ("N", "the article says « le 4 mai »"),
        },
    )
    first = import_labels(paths)
    assert first.appended == 3
    notes = {lab.element_id: lab.note for lab in _labels(paths)}
    assert notes == {
        V2_NAME.element_id: "locuteur",
        PLAIN_QUOTE.element_id: "locuteur",
        V2_DATE.element_id: "",
    }
    raw = paths.labels.read_text(encoding="utf-8")
    assert "mauvais" not in raw.casefold() and "article says" not in raw

    again = import_labels(paths)  # the same sheet, free text still in it: nothing new
    assert (again.appended, again.changed) == (0, 0)
    assert len(_labels(paths)) == 3

    result, record = check_labels(paths)  # check 3 still sees the marker
    assert [r.key for r in result.locuteur_notes] == [_key(V2_NAME)]
    assert record.locuteur_notes == 1


def test_import_needs_a_sheet(tmp_path: Path) -> None:
    with pytest.raises(CompareError, match="run 'export-labels' first"):
        import_labels(_setup(tmp_path))


def test_a_sheet_not_saved_as_utf8_is_refused(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    export_labels(paths)
    text = paths.label_sheet.read_text(encoding="utf-8-sig")
    paths.label_sheet.write_bytes(text.replace(";;", ";S;é", 1).encode("cp1252"))
    with pytest.raises(CompareError, match="save it as \"CSV UTF-8\" with ';' as separator"):
        import_labels(paths)
    assert not paths.labels.exists()


# --- check-labels ----------------------------------------------------------------------------


def _label(e: ElementRecord, label: Label, note: str = "") -> HumanLabel:
    return HumanLabel(element_id=e.element_id, label=label, note=note)


V2_QUOTE_FOUND = _element("tsa-02", "v2", "Nous viendrons", "quote")
PLAIN_DATE = _element("tsa-02", "plain_prompt", "3 MAI", "date")


def _checked(tmp_path: Path, given: list[HumanLabel]) -> tuple[Any, CheckRecord]:
    paths = _setup(tmp_path, [*NEW_ARMS, V2_QUOTE_FOUND, PLAIN_DATE])
    write_jsonl(paths.labels, given)
    return check_labels(paths)


def test_check_1_a_supported_quote_the_matcher_did_not_find(tmp_path: Path) -> None:
    result, record = _checked(
        tmp_path, [_label(PLAIN_QUOTE, "SUPPORTED"), _label(V2_QUOTE_FOUND, "SUPPORTED")]
    )
    assert [r.key for r in result.quote_supported_not_found] == [_key(PLAIN_QUOTE)]
    assert record.quote_supported_not_found == 1
    assert record.inconsistent_values == record.locuteur_notes == 0


def test_check_2_same_type_and_normalized_value_with_different_labels(tmp_path: Path) -> None:
    result, record = _checked(
        tmp_path,
        [
            _label(V2_NAME, "SUPPORTED"),
            _label(PLAIN_NAME, "CONTRADICTED"),  # same item, type and value: flagged with V2_NAME
            _label(V2_DATE, "SUPPORTED"),
            _label(PLAIN_DATE, "NOT_IN_SOURCE"),  # "3 MAI" normalizes to "3 mai"
            _label(PAGE_TEXT_PLACE, "SUPPORTED"),
        ],
    )
    dates = sorted([_key(V2_DATE), _key(PLAIN_DATE)])
    names = sorted([_key(V2_NAME), _key(PLAIN_NAME)])
    assert [r.key for r in result.inconsistent_values] == dates + names  # group after group
    assert record.inconsistent_values == 4

    agreeing, _ = _checked(
        tmp_path / "agree", [_label(V2_NAME, "SUPPORTED"), _label(PLAIN_NAME, "SUPPORTED")]
    )
    assert agreeing.inconsistent_values == []


def test_check_3_locuteur_outside_a_contradicted_quote(tmp_path: Path) -> None:
    result, record = _checked(
        tmp_path,
        [
            _label(PLAIN_QUOTE, "CONTRADICTED", "locuteur"),  # the expected use
            _label(V2_QUOTE_FOUND, "SUPPORTED", "Locuteur à vérifier"),
            _label(V2_NAME, "CONTRADICTED", "mauvais LOCUTEUR"),
            _label(PLAIN_NAME, "CONTRADICTED", "orthographe"),
        ],
    )
    assert [r.key for r in result.locuteur_notes] == [_key(V2_QUOTE_FOUND), _key(V2_NAME)]
    assert record.locuteur_notes == 2 and record.n_labels == 4


def test_checks_read_the_latest_labels_of_the_population_only(tmp_path: Path) -> None:
    _, record = _checked(
        tmp_path,
        [
            _label(PLAIN_QUOTE, "SUPPORTED"),
            _label(PLAIN_QUOTE, "CONTRADICTED"),  # relabeled: no longer flagged
            _label(PAGE_TEXT_LABELED, "SUPPORTED", "locuteur"),  # labeled in the baseline
        ],
    )
    assert (record.n_labels, record.quote_supported_not_found, record.locuteur_notes) == (1, 0, 0)


def test_check_labels_logs_counts_and_prints_items_and_keys_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _setup(tmp_path, [*NEW_ARMS, V2_QUOTE_FOUND, PLAIN_DATE])
    given = [
        _label(PLAIN_QUOTE, "SUPPORTED", "locuteur"),
        _label(V2_NAME, "SUPPORTED"),
        _label(PLAIN_NAME, "NOT_IN_SOURCE"),
        _label(PAGE_TEXT_PLACE, "SUPPORTED"),
    ]
    write_jsonl(paths.labels, given)
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    compare_main.main(["check-labels"])
    out = capsys.readouterr().out

    names = sorted([_key(V2_NAME), _key(PLAIN_NAME)])
    assert out.splitlines() == [
        "Checked 4 labels.",
        "Check 1, quotes labeled SUPPORTED that the matcher did not find: 1",
        f"  tsa-00 {_key(PLAIN_QUOTE)}",
        "Check 2, rows of one article with the same type and normalized value but different "
        "labels: 2",
        *(f"  tsa-00 {k}" for k in names),
        "Check 3, 'locuteur' in the note of a row that is not a quote labeled CONTRADICTED: 1",
        f"  tsa-00 {_key(PLAIN_QUOTE)}",
    ]
    [record] = read_jsonl(paths.check_log, CheckRecord)
    assert (record.n_labels, record.quote_supported_not_found) == (4, 1)
    assert (record.inconsistent_values, record.locuteur_notes) == (2, 1)
    _assert_blind(out, [*NEW_ARMS, V2_QUOTE_FOUND, PLAIN_DATE, PAGE_TEXT_PLACE])


def _assert_blind(out: str, elements: list[ElementRecord]) -> None:
    for hidden in ("v2", "plain_prompt", "page_text", "NOT_IN_SOURCE", "Karim", "Nous"):
        assert hidden not in out
    assert all(e.element_id not in out for e in elements)


def test_export_and_import_print_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _setup(tmp_path)
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    compare_main.main(["export-labels"])
    _fill(paths.label_sheet, {_key(V2_NAME): ("S", ""), _key(PAGE_TEXT_NAME): ("N", "")})
    compare_main.main(["import-labels"])
    out = capsys.readouterr().out

    assert out.splitlines() == [
        f"6 rows to label -> {paths.label_sheet}",
        "Appended 2 labels, of which 0 changed; 2 labeled, 4 pending in the population.",
    ]
    _assert_blind(out.replace(str(paths.label_sheet), ""), POPULATION)


def test_the_cli_prints_an_import_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _setup(tmp_path)
    monkeypatch.setattr(ComparePaths, "default", lambda: paths)
    compare_main.main(["export-labels"])
    _fill(paths.label_sheet, {}, ["kffffffffff"])
    with pytest.raises(SystemExit, match="unknown key kffffffffff"):
        compare_main.main(["import-labels"])
