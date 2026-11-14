import csv
from pathlib import Path

import pytest

from evals.baseline_v1 import __main__ as baseline_cli
from evals.baseline_v1.__main__ import cmd_export_labels, cmd_import_labels, main
from evals.baseline_v1.jsonl import read_jsonl, write_jsonl
from evals.baseline_v1.labeling import (
    SPEAKER_MARKER,
    committed_note,
    in_audit_sample,
    parse_label,
    read_label_sheet,
    select_for_labeling,
    select_unlabeled_found,
    write_label_sheet,
)
from evals.baseline_v1.paths import Paths
from evals.baseline_v1.schemas import (
    Condition,
    ElementRecord,
    HumanLabel,
    MatchStatus,
    SampleItem,
    Task,
)


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


# --- ADR-0016, point 2: a sheet for the unlabeled 'found' elements of one cell ---------------


def _cell_element(
    element_id: str,
    status: MatchStatus | None,
    *,
    item_id: str = "a",
    condition: Condition = "url_only",
    task: Task = "article",
) -> ElementRecord:
    return ElementRecord(
        element_id=element_id,
        run_id=f"{item_id}:{condition}:{task}",
        item_id=item_id,
        condition=condition,
        task=task,
        type="person",
        value="Tebboune",
        context="Le président Tebboune",
        match_status=status,
        match_score=None if status is None else 100.0,
    )


def test_select_unlabeled_found_keeps_one_cell_only() -> None:
    elements = [
        _cell_element("keep2", "found", item_id="b"),
        _cell_element("keep1", "found"),
        _cell_element("labeled", "found"),
        _cell_element("not_found", "not_found"),
        _cell_element("opaque", "found", item_id="a-opaque"),
        _cell_element("summary", "found", task="summary"),
        _cell_element("page_text", "found", condition="page_text"),
    ]
    selected = select_unlabeled_found(
        elements,
        already_labeled={"labeled"},
        condition="url_only",
        task="article",
        excluded_items={"a-opaque"},
    )
    # Every 'found' element of the cell, not a hash sample, sorted like the default sheet.
    assert [e.element_id for e in selected] == ["keep1", "keep2"]


def _write_inputs(
    paths: Paths, elements: list[ElementRecord], labels: list[HumanLabel] | None = None
) -> None:
    write_jsonl(
        paths.sample,
        [
            SampleItem(item_id="a", source="tsa", url="u", title="t", snapshot_id="a"),
            SampleItem(item_id="b", source="tsa", url="v", title="t", snapshot_id="b"),
            SampleItem(item_id="a-opaque", source="tsa", url="u?p=1", title="t",
                       snapshot_id="a", is_opaque=True),
        ],
    )  # fmt: skip
    write_jsonl(paths.elements, elements)
    write_jsonl(paths.labels, labels or [])


def _sheet_ids(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row["element_id"] for row in csv.DictReader(f, delimiter=";")]


_CELL_ELEMENTS = [
    _cell_element("f1", "found"),
    _cell_element("f2", "found", item_id="b"),
    _cell_element("old", "found"),
    _cell_element("nf", "not_found"),
    _cell_element("op", "found", item_id="a-opaque"),
    _cell_element("sum", "found", task="summary"),
]


def test_export_cell_writes_its_own_sheet_and_leaves_the_default_one_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = Paths(dataset_dir=tmp_path / "dataset", data_dir=tmp_path / "data")
    _write_inputs(paths, _CELL_ELEMENTS, [HumanLabel(element_id="old", label="SUPPORTED")])
    _sheet_with_notes(paths.label_sheet, {"x#0": "une note locale"})
    before = paths.label_sheet.read_bytes()

    cmd_export_labels(paths, cell=("url_only", "article"))

    assert paths.label_sheet.read_bytes() == before
    sheet = paths.cell_label_sheet("url_only", "article")
    assert sheet == paths.data_dir / "to_label_url_only_article.csv"
    assert _sheet_ids(sheet) == ["f1", "f2"]
    out = capsys.readouterr().out
    assert f"2 elements to label (url_only / article) -> {sheet}" in out
    assert "not blind" in out and "baseline" in out
    assert "import-labels --cell url_only article" in out


def test_export_cell_needs_matched_elements(tmp_path: Path) -> None:
    paths = Paths(dataset_dir=tmp_path / "dataset", data_dir=tmp_path / "data")
    _write_inputs(paths, [_cell_element("f1", None)])
    with pytest.raises(SystemExit, match="not matched yet"):
        cmd_export_labels(paths, cell=("url_only", "article"))
    assert not paths.cell_label_sheet("url_only", "article").exists()


@pytest.mark.parametrize("cell", [None, ("url_only", "article")])
def test_export_refuses_to_overwrite_a_sheet_without_force(
    tmp_path: Path, cell: tuple[Condition, Task] | None
) -> None:
    paths = Paths(dataset_dir=tmp_path / "dataset", data_dir=tmp_path / "data")
    _write_inputs(paths, _CELL_ELEMENTS)
    sheet = paths.label_sheet if cell is None else paths.cell_label_sheet(*cell)
    _sheet_with_notes(sheet, {"x#0": "une note locale"})
    before = sheet.read_bytes()

    with pytest.raises(SystemExit, match="labels or notes not yet imported") as exc:
        cmd_export_labels(paths, cell=cell)
    assert str(sheet) in str(exc.value.code) and "--force" in str(exc.value.code)
    assert sheet.read_bytes() == before

    cmd_export_labels(paths, cell=cell, force=True)
    assert sheet.read_bytes() != before
    assert "x#0" not in _sheet_ids(sheet)


def test_export_default_sheet_output_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = Paths(dataset_dir=tmp_path / "dataset", data_dir=tmp_path / "data")
    _write_inputs(paths, [_cell_element("nf", "not_found")])
    cmd_export_labels(paths)
    assert _sheet_ids(paths.label_sheet) == ["nf"]
    assert capsys.readouterr().out == (
        f"1 elements to label -> {paths.label_sheet}\n"
        "Fill the 'label' column with S (SUPPORTED), C (CONTRADICTED) or N (NOT_IN_SOURCE),\n"
        "judging against the reviewed snapshot text only, then run 'import-labels'.\n"
    )


def test_cell_round_trip_adds_labels_next_to_the_older_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = Paths(dataset_dir=tmp_path / "dataset", data_dir=tmp_path / "data")
    old = HumanLabel(element_id="old", label="SUPPORTED")
    _write_inputs(paths, _CELL_ELEMENTS, [old])
    cmd_export_labels(paths, cell=("url_only", "article"))
    sheet = paths.cell_label_sheet("url_only", "article")
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for row in rows:
        row["label"], row["note"] = ("N" if row["element_id"] == "f1" else "S"), "texte libre"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    capsys.readouterr()

    cmd_import_labels(paths, cell=("url_only", "article"))

    imported = read_jsonl(paths.labels, HumanLabel)
    assert {(lab.element_id, lab.label, lab.note) for lab in imported} == {
        ("old", "SUPPORTED", ""),
        ("f1", "NOT_IN_SOURCE", ""),
        ("f2", "SUPPORTED", ""),
    }
    assert capsys.readouterr().out == f"Imported 2 labels, 3 total -> {paths.labels}\n"
    assert not paths.label_sheet.exists()

    cmd_export_labels(paths, cell=("url_only", "article"), force=True)
    assert _sheet_ids(sheet) == []
    assert capsys.readouterr().out.startswith("0 elements to label (url_only / article)")


@pytest.mark.parametrize(
    "argv",
    [
        ["export-labels", "--cell", "plain_prompt", "article"],
        ["export-labels", "--cell", "url_only", "blog"],
        ["export-labels", "--cell", "url_only"],
        ["import-labels", "--cell", "v2", "summary"],
        ["import-labels", "--cell", "page_text", "articles"],
    ],
)
def test_an_invalid_cell_is_rejected(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2  # argparse usage error, before any file is touched
    assert "--cell" in capsys.readouterr().err


def test_a_valid_cell_reaches_the_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        baseline_cli,
        "cmd_export_labels",
        lambda paths, cell=None, force=False: seen.append(("export", cell, force)),
    )
    monkeypatch.setattr(
        baseline_cli,
        "cmd_import_labels",
        lambda paths, cell=None: seen.append(("import", cell, None)),
    )
    main(["export-labels", "--cell", "page_text", "summary", "--force"])
    main(["export-labels"])
    main(["import-labels", "--cell", "url_only", "article"])
    main(["import-labels"])
    assert seen == [
        ("export", ("page_text", "summary"), True),
        ("export", None, False),
        ("import", ("url_only", "article"), None),
        ("import", None, None),
    ]
