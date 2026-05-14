"""Audit 4.6 — verify escapeHtml in public/index.html actually neutralizes payloads.

The Wave 4.6 commit chose lint-only enforcement on the assumption that the
existing 17 `${escapeHtml(...)}` interpolation sites are already safe. That
safety rides entirely on `escapeHtml()` correctly mapping `& < > " '` to their
HTML entities. This test extracts the live function from public/index.html
and runs known XSS payloads through it via Node so the assumption can never
silently break.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parent.parent / "public" / "index.html"


def _extract_escape_html_fn() -> str:
    text = INDEX_HTML.read_text(encoding="utf-8")
    start = text.find("function escapeHtml(s) {")
    assert start >= 0, "escapeHtml function start not found in public/index.html"
    brace_open = text.find("{", start)
    depth = 0
    for i in range(brace_open, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("unmatched braces extracting escapeHtml")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize(
    "payload,expected",
    [
        ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
        ("<img src=x onerror=alert(1)>", "&lt;img src=x onerror=alert(1)&gt;"),
        ('"><script>alert(1)</script>', "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"),
        ("' OR 1=1 --", "&#39; OR 1=1 --"),
        ("plain text", "plain text"),
        ("Tom & Jerry", "Tom &amp; Jerry"),
        ("", ""),
        (None, ""),
    ],
)
def test_escape_html_neutralizes_payload(payload: str | None, expected: str) -> None:
    fn = _extract_escape_html_fn()
    js_payload = json.dumps(payload)
    script = f"{fn}\nprocess.stdout.write(escapeHtml({js_payload}));"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    assert result.stdout == expected


def test_escape_html_is_present_and_well_formed() -> None:
    """Smoke check that runs even without Node — guards the extractor itself."""
    fn = _extract_escape_html_fn()
    # All five HTML entities the function is supposed to map.
    assert "&amp;" in fn
    assert "&lt;" in fn
    assert "&gt;" in fn
    assert "&quot;" in fn
    assert "&#39;" in fn
