"""Prune legacy n-gram-era predictions from data/predictions.jsonl.

Background: pre-2026-05-14, the pipeline used n-gram normalization
(pipeline/normalize.py before commit b1622b0) which produced single-token
garbage keywords like 'llm', 'ai', 'model', 'agent', 'language', 'memory'.
After PR #9's embedding-cosine fallback these rows are harmless — they
fail to match any real topic and stay verdict=pending forever — but they
bloat data/predictions.jsonl and add noise to scripts/backtest_predictions.py.

Default is dry-run: prints what would be removed without touching the file.
Use --apply to actually rewrite; a .bak backup is written first.

Criteria (intentionally conservative — never delete a real datapoint):
  - filed_at < CUTOFF (default 2026-05-14, the day the topic primitive switched)
  AND
  - verdict == "pending" (verified / verified_early / wrong / tracking are
    real hit-rate data and must NEVER be dropped, even pre-cutoff)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

# 2026-05-14 = commit b1622b0 (fix(pipeline): replace n-gram normalization
# with Claude-extracted topics). Predictions filed strictly before this
# date are guaranteed n-gram-era output.
CUTOFF = "2026-05-14"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "predictions.jsonl"


def is_legacy_pending(row: dict, cutoff: str = CUTOFF) -> bool:
    """A row is droppable iff it's old AND still pending.

    Verified / verified_early / wrong / tracking rows are real hit-rate
    data and are never droppable regardless of date. Missing or empty
    filed_at is treated as "unknown" and never dropped — better to keep
    a mystery row than to silently lose data we can't date.
    """
    filed_at_raw = row.get("filed_at")
    if not filed_at_raw:
        return False
    filed_at = filed_at_raw[:10]
    verdict = row.get("verdict", "")
    return filed_at < cutoff and verdict == "pending"


def partition(rows: list[dict], cutoff: str = CUTOFF) -> tuple[list[dict], list[dict]]:
    """Split rows into (keep, drop). Preserves original order in each list."""
    keep: list[dict] = []
    drop: list[dict] = []
    for r in rows:
        (drop if is_legacy_pending(r, cutoff) else keep).append(r)
    return keep, drop


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(payload, encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prune n-gram-era pending predictions from data/predictions.jsonl. "
            "Dry-run by default."
        )
    )
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument(
        "--cutoff",
        default=CUTOFF,
        help=f"YYYY-MM-DD; rows filed before this date can be dropped (default {CUTOFF})",
    )
    parser.add_argument(
        "--apply", action="store_true", help="rewrite the file (default: dry-run)"
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"file not found: {args.path}", file=sys.stderr)
        return 1

    rows = load_rows(args.path)
    keep, drop = partition(rows, args.cutoff)

    print(f"file:    {args.path}")
    print(f"cutoff:  filed_at < {args.cutoff} AND verdict == 'pending'")
    print(f"total:   {len(rows)} rows")
    print(f"keep:    {len(keep)} rows")
    print(f"drop:    {len(drop)} rows")
    print()

    if drop:
        print("Rows that would be dropped:")
        for r in drop:
            filed = (r.get("filed_at") or "?")[:10]
            kw = r.get("keyword", "?")
            verdict = r.get("verdict", "?")
            print(f"  - {filed}  keyword={kw!r:<24} verdict={verdict}")
        print()

    if not drop:
        print("Nothing to do.")
        return 0

    if not args.apply:
        print("Dry-run only. Pass --apply to rewrite the file.")
        return 0

    backup = args.path.with_suffix(args.path.suffix + ".bak")
    shutil.copy2(args.path, backup)
    write_jsonl(args.path, keep)
    print(f"Wrote {len(keep)} rows to {args.path}.")
    print(f"Original preserved at {backup}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
