#!/usr/bin/env python3
"""One-shot historical backfill for Digg AI corpus.

Uses pipeline.fetch.digg.fetch_digg_historical_stories() which scrolls the
digg.com/ai page via Firecrawl's `actions` API to reveal the
"Yesterday's Top Stories" sections. Each section gets its own date label so
observations carry the correct day attribution.

Run after fresh checkout to seed the corpus, or weekly to top up coverage:

    poetry run python scripts/backfill_digg.py
    poetry run python scripts/backfill_digg.py --scroll 10   # go deeper

Day-to-day refresh uses `python -m pipeline.fetch.digg` instead — cheaper,
more reliable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env.local", override=False)

from pipeline.fetch import digg  # noqa: E402 — path shim + env first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scroll",
        type=int,
        default=6,
        help="Number of scroll-to-bottom passes (default 6 ≈ today + yesterday)",
    )
    args = parser.parse_args()

    print(f"Backfilling Digg corpus with {args.scroll} scroll passes...")
    stories = digg.fetch_digg_historical_stories(scroll_count=args.scroll)
    if not stories:
        print("⚠ No stories fetched. Check FIRECRAWL_API_KEY and digg.com/ai.", file=sys.stderr)
        return 1

    # Group by snapshot_date to show what we got per section
    by_date: dict[str, int] = {}
    for s in stories:
        by_date[s.snapshot_date or "(no date)"] = by_date.get(s.snapshot_date or "(no date)", 0) + 1

    corpus = digg.update_corpus(stories)
    print(f"✓ Pulled {len(stories)} story observations across {len(by_date)} section(s):")
    for date, n in by_date.items():
        print(f"    {date:>30s}  {n:>3d} stories")
    print(f"✓ Corpus now has {corpus['story_count']} unique stories")
    print(f"  → {digg.CORPUS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
