import csv
from pathlib import Path

import pytest

from evals.baseline_v1.__main__ import cmd_import_labels
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.labeling import (
    SPEAKER_MARKER,
    committed_note,
    in_audit_sample,
    parse_label,
    read_label_sheet,
    select_for_labeling,
    write_label_sheet,
)
from evals.baseline_v1.paths import Paths
from evals.baseline_v1.schemas import ElementRecord, HumanLabel


def _element(element_id: str, status: str) -> ElementRecord:
    return ElementRecord(
        element_id=element_id,
        run_id=element_id.split("#")[0],
        item_id="tsa-00",
        condition="url_only",
        task="summary",
        type="person",
        value="Tebboune",
        context="Le président Tebboune",
        match_status=status,
        match_score=100.0 if status == "found" else 0.0,
    )


def test_audit_sample_is_stable_and_close_to_rate() -> None:
    ids = [f"run:{i}#0" for i in range(2000)]
    share = sum(in_audit_sample(i) for i in ids) / len(ids)
    assert 0.17 < share < 0.23
    assert [in_audit_sample(i) for i in ids] == [in_audit_sample(i) for i in ids]


def test_select_takes_all_not_found_and_skips_already_labeled() -> None:
    elements = [_element(f"r{i}#0", "not_found") for i in range(5)]
    elements += [_element(f"f{i}#0", "found") for i in range(200)]
    selected = select_for_labeling(elements, already_labeled={"r0#0"})
    ids = {e.element_id for e in selected}

    assert {f"r{i}#0" for i in range(1, 5)} <= ids
    assert "r0#0" not in ids
    assert 0 < sum(e.match_status == "found" for e in selected) < 200


def test_label_sheet_round_trip(tmp_path: Path) -> None:
    sheet = tmp_path / "to_label.csv"
    write_label_sheet(sheet, [_element("a#0", "not_found"), _element("b#0", "not_found")])
    content = sheet.read_text(encoding="utf-8-sig").replace("not_found;0;;", "not_found;0;n;", 1)
    sheet.write_text(content, encoding="utf-8-sig")

    labels = read_label_sheet(sheet)
    assert [(lab.element_id, lab.label) for lab in labels] == [("a#0", "NOT_IN_SOURCE")]


def _sheet_with_notes(path: Path, notes: dict[str, str]) -> None:
    """A sheet whose rows (one per element id) are labeled S with the given note."""
    write_label_sheet(path, [_element(element_id, "not_found") for element_id in notes])
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for row in rows:
        row["label"], row["note"] = "S", notes[row["element_id"]]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def test_committed_note_keeps_only_the_speaker_marker() -> None:
    assert SPEAKER_MARKER == "locuteur"
    assert committed_note("the article says « le 4 mai »") == ""
    assert committed_note("Mauvais LOCUTEUR : Ali") == "locuteur"
    assert committed_note(" Locuteur ") == "locuteur"
    assert committed_note("") == ""


def test_the_sheet_note_is_reduced_on_read(tmp_path: Path) -> None:
    sheet = tmp_path / "to_label.csv"
    _sheet_with_notes(sheet, {"a#0": "texte libre", "b#0": "mauvais Locuteur : X", "c#0": ""})
    notes = {lab.element_id: lab.note for lab in read_label_sheet(sheet)}
    assert notes == {"a#0": "", "b#0": "locuteur", "c#0": ""}


def test_a_sheet_without_a_note_cell_gives_an_empty_note(tmp_path: Path) -> None:
    sheet = tmp_path / "to_label.csv"
    sheet.write_text("element_id;label\na#0;S\n", encoding="utf-8-sig")
    assert [lab.note for lab in read_label_sheet(sheet)] == [""]


def test_import_rewrites_every_label_without_free_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = Paths(dataset_dir=tmp_path / "dataset", data_dir=tmp_path / "data")
    old = [
        HumanLabel(element_id="old#0", label="CONTRADICTED", note="texte cité de l'article"),
        HumanLabel(element_id="kept#0", label="CONTRADICTED", note="mauvais locuteur : X"),
    ]
    write_jsonl(paths.labels, old)
    _sheet_with_notes(paths.label_sheet, {"a#0": "texte libre"})

    cmd_import_labels(paths)

    assert {(lab.element_id, lab.note) for lab in read_jsonl(paths.labels, HumanLabel)} == {
        ("old#0", ""),
        ("kept#0", "locuteur"),
        ("a#0", ""),
    }
    assert "texte" not in paths.labels.read_text(encoding="utf-8")
    assert capsys.readouterr().out == f"Imported 1 labels, 3 total -> {paths.labels}\n"


def test_parse_label_rejects_unknown_values() -> None:
    assert parse_label(" s ") == "SUPPORTED"
    with pytest.raises(ValueError, match="Unknown label"):
        parse_label("maybe")
