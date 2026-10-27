from pathlib import Path

import pytest

from evals.baseline_v1.labeling import (
    in_audit_sample,
    parse_label,
    read_label_sheet,
    select_for_labeling,
    write_label_sheet,
)
from evals.baseline_v1.schemas import ElementRecord


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


def test_parse_label_rejects_unknown_values() -> None:
    assert parse_label(" s ") == "SUPPORTED"
    with pytest.raises(ValueError, match="Unknown label"):
        parse_label("maybe")
