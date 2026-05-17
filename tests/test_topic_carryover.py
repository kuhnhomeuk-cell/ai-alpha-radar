"""Tests for scripts/topic_carryover.py.

Locks in the day-over-day overlap math, the bias-date gating, the pair
generator, and the text renderer's PASS / FAIL / NEEDS-MORE-DATA verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import topic_carryover  # noqa: E402


def _write_snapshot(tmp_path: Path, date: str, keywords: list[str]) -> Path:
    """Write a minimal Snapshot-shaped JSON file with the given keywords."""
    snap = {
        "snapshot_date": date,
        "generated_at": f"{date}T06:00:00Z",
        "trends": [{"keyword": k} for k in keywords],
    }
    p = tmp_path / f"{date}.json"
    p.write_text(json.dumps(snap), encoding="utf-8")
    return p


def test_keywords_for_snapshot_reads_keyword_field(tmp_path):
    p = _write_snapshot(tmp_path, "2026-05-13", ["alpha", "beta", "gamma"])
    assert topic_carryover.keywords_for_snapshot(p) == ["alpha", "beta", "gamma"]


def test_keywords_for_snapshot_skips_missing_keyword(tmp_path):
    raw = {"trends": [{"keyword": "alpha"}, {"keyword": ""}, {"keyword": "beta"}]}
    p = tmp_path / "2026-05-13.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert topic_carryover.keywords_for_snapshot(p) == ["alpha", "beta"]


def test_overlap_ratio_full_carry():
    common, total, ratio = topic_carryover.overlap_ratio(["a", "b", "c"], ["a", "b", "c"])
    assert (common, total, ratio) == (3, 3, 1.0)


def test_overlap_ratio_zero_carry():
    common, total, ratio = topic_carryover.overlap_ratio(["a", "b"], ["x", "y"])
    assert (common, total, ratio) == (0, 2, 0.0)


def test_overlap_ratio_half_carry():
    common, total, ratio = topic_carryover.overlap_ratio(
        ["a", "b", "c", "d"], ["a", "b", "e", "f"]
    )
    assert (common, total, ratio) == (2, 4, 0.5)


def test_overlap_ratio_case_insensitive():
    common, _, ratio = topic_carryover.overlap_ratio(["Alpha", "BETA"], ["alpha", "beta"])
    assert common == 2
    assert ratio == 1.0


def test_overlap_ratio_dedupes_within_snapshot():
    # curr has a duplicate; it should count once
    common, total, _ = topic_carryover.overlap_ratio(["a"], ["a", "a"])
    assert (common, total) == (1, 1)


def test_overlap_ratio_empty_curr_is_safe():
    common, total, ratio = topic_carryover.overlap_ratio(["a"], [])
    assert (common, total, ratio) == (0, 0, 0.0)


def test_walk_snapshots_sorted_by_date(tmp_path):
    _write_snapshot(tmp_path, "2026-05-15", ["x"])
    _write_snapshot(tmp_path, "2026-05-13", ["x"])
    _write_snapshot(tmp_path, "2026-05-14", ["x"])
    snaps = topic_carryover.walk_snapshots(tmp_path)
    assert [p.stem for p in snaps] == ["2026-05-13", "2026-05-14", "2026-05-15"]


def test_walk_snapshots_filters_by_from_and_to(tmp_path):
    for d in ["2026-05-13", "2026-05-14", "2026-05-15", "2026-05-16"]:
        _write_snapshot(tmp_path, d, ["x"])
    snaps = topic_carryover.walk_snapshots(
        tmp_path, from_date="2026-05-14", to_date="2026-05-15"
    )
    assert [p.stem for p in snaps] == ["2026-05-14", "2026-05-15"]


def test_walk_snapshots_ignores_non_date_files(tmp_path):
    _write_snapshot(tmp_path, "2026-05-13", ["x"])
    (tmp_path / ".gitkeep").write_text("")
    (tmp_path / "README.md").write_text("noise")
    snaps = topic_carryover.walk_snapshots(tmp_path)
    assert [p.stem for p in snaps] == ["2026-05-13"]


def test_compute_pairs_marks_post_bias(tmp_path):
    paths = [
        _write_snapshot(tmp_path, "2026-05-15", ["a", "b"]),
        _write_snapshot(tmp_path, "2026-05-16", ["a", "c"]),  # pre-bias curr
        _write_snapshot(tmp_path, "2026-05-17", ["a", "c"]),  # post-bias curr (BIAS_FIX_DATE)
        _write_snapshot(tmp_path, "2026-05-18", ["a", "d"]),  # post-bias curr
    ]
    pairs = topic_carryover.compute_pairs(paths)
    assert len(pairs) == 3
    assert pairs[0]["post_bias"] is False  # curr=2026-05-16
    assert pairs[1]["post_bias"] is True  # curr=2026-05-17
    assert pairs[2]["post_bias"] is True  # curr=2026-05-18
    # Carry-over numbers
    assert (pairs[0]["common"], pairs[0]["curr_total"]) == (1, 2)
    assert (pairs[1]["common"], pairs[1]["curr_total"]) == (2, 2)


def test_render_text_shows_needs_more_data_when_under_three_post_bias(tmp_path):
    paths = [
        _write_snapshot(tmp_path, "2026-05-16", ["a"]),
        _write_snapshot(tmp_path, "2026-05-17", ["a"]),  # 1 post-bias pair
    ]
    pairs = topic_carryover.compute_pairs(paths)
    text = topic_carryover.render_text(pairs)
    assert "NEEDS 2 more" in text


def test_render_text_shows_pass_when_three_post_bias_above_target(tmp_path):
    paths = [
        _write_snapshot(tmp_path, "2026-05-16", ["a", "b", "c"]),
        _write_snapshot(tmp_path, "2026-05-17", ["a", "b", "c"]),
        _write_snapshot(tmp_path, "2026-05-18", ["a", "b", "c"]),
        _write_snapshot(tmp_path, "2026-05-19", ["a", "b", "c"]),
    ]
    pairs = topic_carryover.compute_pairs(paths)
    text = topic_carryover.render_text(pairs)
    assert "PASS" in text


def test_render_text_shows_fail_when_three_post_bias_below_target(tmp_path):
    paths = [
        _write_snapshot(tmp_path, "2026-05-16", ["a", "b", "c", "d"]),
        _write_snapshot(tmp_path, "2026-05-17", ["w", "x", "y", "z"]),
        _write_snapshot(tmp_path, "2026-05-18", ["w", "x", "y", "z"]),
        _write_snapshot(tmp_path, "2026-05-19", ["m", "n", "o", "p"]),
    ]
    pairs = topic_carryover.compute_pairs(paths)
    # post_bias pairs: 16→17 ratio 0, 17→18 ratio 1.0, 18→19 ratio 0 → avg 0.33 < 0.5
    text = topic_carryover.render_text(pairs, target=0.5)
    assert "FAIL" in text


def test_main_returns_one_when_too_few_snapshots(tmp_path, capsys):
    _write_snapshot(tmp_path, "2026-05-13", ["a"])
    rc = topic_carryover.main(["--snapshots-dir", str(tmp_path)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "at least 2 snapshots" in captured.err


def test_main_json_mode_emits_pair_objects(tmp_path, capsys):
    _write_snapshot(tmp_path, "2026-05-16", ["a", "b"])
    _write_snapshot(tmp_path, "2026-05-17", ["a", "c"])
    rc = topic_carryover.main(["--snapshots-dir", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pairs"][0]["prev_date"] == "2026-05-16"
    assert payload["pairs"][0]["curr_date"] == "2026-05-17"
    assert payload["pairs"][0]["post_bias"] is True
