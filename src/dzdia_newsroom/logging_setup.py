"""One JSON object per log line (ADR-005), so `docker logs` output can be filtered by field.

Context goes in `extra`: `logger.info("job done", extra={"job_id": 42, "latency_ms": 120})`.
Never log secrets or message text.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Literal

# Attributes every LogRecord has; anything else on a record came from `extra`.
_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
# uvicorn's copy of the message with ANSI colour codes: noise in JSON.
_DROPPED = _RECORD_ATTRS | {"color_message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _DROPPED and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(log_format: Literal["json", "text"] = "json", level: str = "INFO") -> None:
    """Install a single stderr handler on the root logger. Call once, at process start."""
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn is started with `log_config=None`; its loggers just propagate to the root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # httpx logs every request URL at INFO, and Bot API URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
