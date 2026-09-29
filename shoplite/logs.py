"""Structured JSON-lines logging: one line per request, tailed by Sentinel."""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = dict(getattr(record, "fields", {}))
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "service": fields.pop("service", "shoplite"),
            "level": record.levelname,
            **fields,
        }
        return json.dumps(entry)


def get_logger(path: Path) -> logging.Logger:
    logger = logging.getLogger("shoplite")
    if logger.handlers:
        return logger
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    file_handler = logging.FileHandler(path, encoding="utf-8")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)  # console shows errors only; the file has everything
    for handler in (file_handler, console_handler):
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
