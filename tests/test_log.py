"""TDD for pipeline.log — structured JSON logging (audit 4.3)."""

from __future__ import annotations

import json
import sys
from io import StringIO

import pytest

from pipeline import log as log_mod


def _parse_lines(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.strip().splitlines() if line.strip()]


def test_log_emits_single_json_line_with_required_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_mod.log("fetch_failed", level="warning", source="github", error="boom")
    captured = capsys.readouterr().err
    lines = _parse_lines(captured)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["event"] == "fetch_failed"
    assert rec["level"] == "warning"
    assert rec["source"] == "github"
    assert rec["error"] == "boom"
    # ts should be parseable as ISO8601
    from datetime import datetime
    datetime.fromisoformat(rec["ts"])


def test_log_default_level_is_info(capsys: pytest.CaptureFixture[str]) -> None:
    log_mod.log("snapshot_written", path="public/data.json")
    rec = _parse_lines(capsys.readouterr().err)[0]
    assert rec["level"] == "info"


def test_log_handles_non_serializable_fields_via_str(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pathlib import Path
    log_mod.log("path_thing", path=Path("/tmp/x"))
    rec = _parse_lines(capsys.readouterr().err)[0]
    assert rec["path"] == "/tmp/x"


def test_log_writes_to_stderr_not_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_mod.log("ping")
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err.strip() != ""
