"""Tests for scripts/prune_legacy_predictions.py.

Locks in the is_legacy_pending criterion, the partition split, and the
dry-run / --apply / backup semantics. Critically: verified and wrong rows
must NEVER be dropped, even if they predate the cutoff — that would skew
the hit-rate metric retroactively.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import prune_legacy_predictions as plp  # noqa: E402


def test_is_legacy_pending_true_when_old_and_pending():
    assert plp.is_legacy_pending({"filed_at": "2026-05-13", "verdict": "pending"})


def test_is_legacy_pending_false_when_at_or_after_cutoff():
    assert not plp.is_legacy_pending({"filed_at": "2026-05-14", "verdict": "pending"})
    assert not plp.is_legacy_pending({"filed_at": "2026-05-16", "verdict": "pending"})


def test_is_legacy_pending_false_when_verified():
    # Critical: verified predictions must never be dropped even pre-cutoff —
    # they're real hit-rate data.
    assert not plp.is_legacy_pending(
        {"filed_at": "2026-05-13", "verdict": "verified"}
    )


def test_is_legacy_pending_false_when_wrong():
    # Same protection for wrong predictions — they're real misses.
    assert not plp.is_legacy_pending({"filed_at": "2026-05-13", "verdict": "wrong"})


def test_is_legacy_pending_false_when_verified_early():
    assert not plp.is_legacy_pending(
        {"filed_at": "2026-05-13", "verdict": "verified_early"}
    )


def test_is_legacy_pending_false_when_tracking():
    assert not plp.is_legacy_pending({"filed_at": "2026-05-13", "verdict": "tracking"})


def test_is_legacy_pending_uses_first_10_chars_of_filed_at():
    # filed_at could be ISO-8601 with time; only the date matters.
    assert plp.is_legacy_pending(
        {"filed_at": "2026-05-13T10:00:00Z", "verdict": "pending"}
    )


def test_is_legacy_pending_false_when_missing_filed_at():
    # Missing filed_at -> "" -> not < cutoff -> safe (keep, don't accidentally drop).
    assert not plp.is_legacy_pending({"verdict": "pending"})


def test_is_legacy_pending_respects_custom_cutoff():
    assert plp.is_legacy_pending(
        {"filed_at": "2026-05-15", "verdict": "pending"}, cutoff="2026-05-16"
    )
    assert not plp.is_legacy_pending(
        {"filed_at": "2026-05-15", "verdict": "pending"}, cutoff="2026-05-15"
    )


def test_partition_separates_drop_from_keep():
    rows = [
        {"filed_at": "2026-05-13", "verdict": "pending", "keyword": "ai"},  # drop
        {"filed_at": "2026-05-13", "verdict": "verified", "keyword": "real"},  # keep
        {"filed_at": "2026-05-16", "verdict": "pending", "keyword": "modern"},  # keep
    ]
    keep, drop = plp.partition(rows)
    assert [r["keyword"] for r in drop] == ["ai"]
    assert {r["keyword"] for r in keep} == {"real", "modern"}


def test_partition_preserves_order_within_keep():
    rows = [
        {"filed_at": "2026-05-16", "verdict": "pending", "keyword": "a"},
        {"filed_at": "2026-05-13", "verdict": "pending", "keyword": "drop_me"},
        {"filed_at": "2026-05-17", "verdict": "pending", "keyword": "b"},
    ]
    keep, _ = plp.partition(rows)
    assert [r["keyword"] for r in keep] == ["a", "b"]


def test_load_rows_skips_blank_lines(tmp_path):
    p = tmp_path / "p.jsonl"
    p.write_text(
        '{"keyword":"a"}\n'
        '\n'
        '{"keyword":"b"}\n'
        '   \n',
        encoding="utf-8",
    )
    rows = plp.load_rows(p)
    assert [r["keyword"] for r in rows] == ["a", "b"]


def test_main_dry_run_does_not_modify_file(tmp_path):
    p = tmp_path / "p.jsonl"
    p.write_text(
        '{"filed_at":"2026-05-13","verdict":"pending","keyword":"ai"}\n'
        '{"filed_at":"2026-05-16","verdict":"pending","keyword":"good"}\n',
        encoding="utf-8",
    )
    rc = plp.main(["--path", str(p)])
    assert rc == 0
    # File still has both rows
    assert p.read_text(encoding="utf-8").count("\n") == 2
    # No backup created on dry-run
    assert not p.with_suffix(".jsonl.bak").exists()


def test_main_apply_rewrites_file_and_makes_backup(tmp_path):
    p = tmp_path / "p.jsonl"
    original = (
        '{"filed_at":"2026-05-13","verdict":"pending","keyword":"ai"}\n'
        '{"filed_at":"2026-05-16","verdict":"pending","keyword":"good"}\n'
    )
    p.write_text(original, encoding="utf-8")

    rc = plp.main(["--path", str(p), "--apply"])
    assert rc == 0

    backup = p.with_suffix(".jsonl.bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == original

    pruned_rows = plp.load_rows(p)
    assert len(pruned_rows) == 1
    assert pruned_rows[0]["keyword"] == "good"


def test_main_apply_with_no_drops_is_a_no_op(tmp_path):
    p = tmp_path / "p.jsonl"
    p.write_text(
        '{"filed_at":"2026-05-16","verdict":"pending","keyword":"good"}\n',
        encoding="utf-8",
    )
    rc = plp.main(["--path", str(p), "--apply"])
    assert rc == 0
    # No backup made when there's nothing to drop
    assert not p.with_suffix(".jsonl.bak").exists()


def test_main_file_not_found(tmp_path, capsys):
    rc = plp.main(["--path", str(tmp_path / "missing.jsonl")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err
