"""Topic carry-over measurement — verifies yesterday-keyword bias is sticky.

Background: commit 6073f63 (feat(topics): bias topic extraction toward
yesterday's keywords) was supposed to stabilise day-over-day topic
vocabulary so predict.build_lifecycle_lookup's exact-match path covers
more verdicts and the embedding-cosine fallback (PR #9) becomes less
load-bearing.

This script measures the day-over-day carry-over ratio across snapshots
in public/snapshots/. For each consecutive pair (prev → curr), it counts
how many of curr's keywords also appeared in prev, divided by total
keywords in curr. Target ≥ 50% under normal conditions; pre-bias
baseline was ~0% (see the 2026-05-17 Star Log regression for evidence).

Usage:
    poetry run python scripts/topic_carryover.py
    poetry run python scripts/topic_carryover.py --from 2026-05-15 --to 2026-05-17
    poetry run python scripts/topic_carryover.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

# Commit 6073f63 (yesterday-keyword bias) shipped on this date. The first
# snapshot generated with the bias active is the one whose stem >= this date.
BIAS_FIX_DATE = "2026-05-17"
TARGET_RATIO = 0.50
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOTS_DIR = ROOT / "public" / "snapshots"
_DATE_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def keywords_for_snapshot(snapshot_path: Path) -> list[str]:
    """Extract non-empty trends[].keyword values from a snapshot file."""
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return [
        t["keyword"]
        for t in data.get("trends", [])
        if isinstance(t.get("keyword"), str) and t["keyword"].strip()
    ]


def overlap_ratio(prev: list[str], curr: list[str]) -> tuple[int, int, float]:
    """Day-over-day carry-over of curr's keywords from prev.

    Returns (common, total_curr, ratio):
      - common     = |curr ∩ prev| (case-insensitive, deduped within each side)
      - total_curr = |curr| (deduped, case-insensitive)
      - ratio      = common / total_curr  (0.0 when total_curr == 0)

    Asymmetric on purpose — we measure whether today's topics reuse
    yesterday's labels, not symmetric Jaccard similarity. The bias prompt's
    success criterion is "today's extractor reused yesterday's wording".
    """
    prev_set = {k.casefold() for k in prev}
    curr_set = {k.casefold() for k in curr}
    common = len(curr_set & prev_set)
    total = len(curr_set)
    return common, total, (common / total if total else 0.0)


def walk_snapshots(
    snapshots_dir: Path,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> list[Path]:
    """Sorted list of YYYY-MM-DD.json files in [from_date, to_date]."""
    paths = sorted(
        p for p in snapshots_dir.glob("*.json") if _DATE_STEM.match(p.stem)
    )
    if from_date:
        paths = [p for p in paths if p.stem >= from_date]
    if to_date:
        paths = [p for p in paths if p.stem <= to_date]
    return paths


def compute_pairs(snapshots: list[Path]) -> list[dict]:
    """Compute carry-over for every consecutive pair."""
    out: list[dict] = []
    for prev_path, curr_path in zip(snapshots, snapshots[1:]):
        prev_kws = keywords_for_snapshot(prev_path)
        curr_kws = keywords_for_snapshot(curr_path)
        common, total, ratio = overlap_ratio(prev_kws, curr_kws)
        out.append(
            {
                "prev_date": prev_path.stem,
                "curr_date": curr_path.stem,
                "common": common,
                "curr_total": total,
                "ratio": ratio,
                "post_bias": curr_path.stem >= BIAS_FIX_DATE,
            }
        )
    return out


def render_text(pairs: list[dict], target: float = TARGET_RATIO) -> str:
    """Pretty table + summary verdict (PASS / FAIL / NEEDS-MORE-DATA)."""
    lines: list[str] = []
    lines.append("Topic carry-over — fraction of today's keywords that were in yesterday's")
    lines.append("=" * 76)
    lines.append(
        f"{'Prev → Curr':<27} {'Common/Total':>14} {'Ratio':>8}   Bias"
    )
    lines.append("-" * 76)
    for p in pairs:
        bias_tag = "post-bias" if p["post_bias"] else "pre-bias "
        lines.append(
            f"{p['prev_date']} → {p['curr_date']:<10} "
            f"{p['common']:>4}/{p['curr_total']:<9} "
            f"{p['ratio']*100:>6.1f}%   {bias_tag}"
        )
    lines.append("")
    pre = [p["ratio"] for p in pairs if not p["post_bias"]]
    post = [p["ratio"] for p in pairs if p["post_bias"]]
    if pre:
        lines.append(f"Pre-bias avg  ({len(pre):>2} pairs): {sum(pre)/len(pre)*100:>5.1f}%")
    if post:
        lines.append(f"Post-bias avg ({len(post):>2} pairs): {sum(post)/len(post)*100:>5.1f}%")
        if len(post) >= 3:
            verdict = "PASS" if sum(post) / len(post) >= target else "FAIL"
            lines.append(f"3-day post-bias target ≥{target*100:.0f}%: {verdict}")
        else:
            need = 3 - len(post)
            lines.append(
                f"3-day post-bias target ≥{target*100:.0f}%: "
                f"NEEDS {need} more post-bias pair(s)"
            )
    else:
        lines.append(
            "No post-bias pairs yet — first available once a snapshot ≥ "
            f"{BIAS_FIX_DATE} exists alongside its prior-day snapshot."
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Measure day-over-day keyword carry-over across snapshots. "
            "Verifies the yesterday-keyword bias on topic extraction."
        )
    )
    parser.add_argument("--snapshots-dir", type=Path, default=DEFAULT_SNAPSHOTS_DIR)
    parser.add_argument("--from", dest="from_date", default=None, help="YYYY-MM-DD inclusive")
    parser.add_argument("--to", dest="to_date", default=None, help="YYYY-MM-DD inclusive")
    parser.add_argument("--target", type=float, default=TARGET_RATIO)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    snapshots = walk_snapshots(args.snapshots_dir, args.from_date, args.to_date)
    if len(snapshots) < 2:
        print(
            f"Need at least 2 snapshots in {args.snapshots_dir}; found {len(snapshots)}.",
            file=sys.stderr,
        )
        return 1

    pairs = compute_pairs(snapshots)
    if args.json:
        print(json.dumps({"pairs": pairs, "target": args.target}, indent=2))
    else:
        print(render_text(pairs, args.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
