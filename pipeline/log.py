"""Structured JSON logging for the pipeline (audit 4.3).

One line per event on stderr. Keeps the wire format flat — no nesting —
so it grep-and-pipe-to-jq cleanly from the GitHub Actions log viewer.

Replaces ad-hoc `print(..., file=sys.stderr)` sites throughout
pipeline/run.py, pipeline/summarize.py, and pipeline/predict.py.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def log(event: str, *, level: str = "info", **fields: Any) -> None:
    """Emit one JSON log record to stderr.

    `event` is the machine-readable verb (e.g. "fetch_failed").
    `level` is "info" | "warning" | "error" — informational only,
    not used for filtering yet. Extra **fields are merged into the
    record; non-JSON-serializable values fall through `str()`.
    """
    record: dict[str, Any] = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "event": event,
        "level": level,
    }
    for k, v in fields.items():
        record[k] = v
    sys.stderr.write(json.dumps(record, default=str) + "\n")
