import json
import logging
import sys
from datetime import date

from dzdia_newsroom.logging_setup import JsonFormatter


def record(**extra: object) -> logging.LogRecord:
    return logging.makeLogRecord(
        {"name": "dzdia.test", "levelname": "INFO", "msg": "job %s done", "args": (42,), **extra}
    )


def test_json_line_has_fixed_fields_and_extras() -> None:
    line = JsonFormatter().format(record(job_id=42, latency_ms=120, day=date(2026, 10, 27)))
    entry = json.loads(line)
    assert entry["level"] == "INFO"
    assert entry["logger"] == "dzdia.test"
    assert entry["msg"] == "job 42 done"
    assert entry["ts"].endswith("+00:00")
    assert entry["job_id"] == 42 and entry["latency_ms"] == 120
    assert entry["day"] == "2026-10-27"  # non-JSON values are stringified
    assert "args" not in entry and "levelno" not in entry  # standard attributes are dropped


def test_uvicorn_colour_copy_is_dropped() -> None:
    entry = json.loads(JsonFormatter().format(record(color_message="[36mjob[0m")))
    assert "color_message" not in entry


def test_exception_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        line = JsonFormatter().format(record(exc_info=sys.exc_info()))
    assert "ValueError: boom" in json.loads(line)["exc"]
