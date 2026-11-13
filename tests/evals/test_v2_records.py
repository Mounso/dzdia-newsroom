"""The record fields shared by the tryout and the comparison (`evals/v2/records.py`)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from evals.baseline_v1.jsonl import read_jsonl
from evals.v2.compare.run import CompareRun
from evals.v2.records import AttemptRecord, OutputFields
from evals.v2.tryout import TryoutRun

# A v2 record of the tryout as written before decision U1: no `issue_details` in its attempts.
OLD_TRYOUT_RECORD = {
    "run_id": "tryout-00:v2:b9e5672bfd4d:m",
    "page_id": "tryout-00",
    "arm": "v2",
    "model": "m",
    "prompt_version": "v2-article-1",
    "prompt_fingerprint": "b9e5672bfd4d",
    "settings": {"temperature": 0.0, "response_schema": "ArticleDraft"},
    "created_at": "2026-11-13T10:00:00Z",
    "truncated": False,
    "n_source_chars": 1000,
    "outcome": "ok",
    "n_evidence": 12,
    "attempts": [{"issues": {"no_id": 2}, "input_tokens": 10, "latency_ms": 900}, {"issues": {}}],
    "retried": True,
}


def test_an_old_tryout_record_without_issue_details_still_parses(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_bytes((json.dumps(OLD_TRYOUT_RECORD) + "\n").encode("utf-8"))
    [record] = read_jsonl(path, TryoutRun)
    assert record.attempts == [
        AttemptRecord(issues={"no_id": 2}, input_tokens=10, latency_ms=900),
        AttemptRecord(issues={}),
    ]
    assert all(a.issue_details == [] for a in record.attempts)
    assert record.created_at == datetime(2026, 11, 13, 10, tzinfo=UTC)


@pytest.mark.parametrize("record_class", [TryoutRun, CompareRun])
def test_every_output_field_is_a_field_of_both_run_records(record_class: type[BaseModel]) -> None:
    # `model_copy(update=...)` does not validate: every key must be a declared field.
    assert set(OutputFields.__annotations__) <= set(record_class.model_fields)
