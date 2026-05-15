"""Wave 5 — operator workflow to refresh `data/youtube_outliers.json`.

The pipeline reads `data/youtube_outliers.json` daily. That file is
operator-refreshed by collecting one `vidiq_outliers` MCP response per
keyword in `data/youtube_keywords.json`, then running this script.

VidIQ MCP tools are only callable from a Claude Code session — not from
CI/cron. This script is the boundary between MCP-side collection and the
plain-Python orchestrator. Mirrors the bluesky operator-scheduled pattern.

Usage
-----
1. In a Claude Code session, for each keyword in `data/youtube_keywords.json`:

       vidiq_outliers(
           keyword=kw,
           limit=10,
           contentType="long",
           publishedWithin="thisMonth",
           sort="score",
           # Wave 5 follow-up: drop news/corporate channels that pollute
           # the "AI tools for solo creators" niche (e.g. CNBC, Bloomberg,
           # Cadence Design Systems, large media outlets).
           maxSubscribers=2_000_000,
       )

   Collect all responses into one JSON object keyed by keyword:

       {
         "AI tools for creators": {"videos": [...], "keyword": "..."},
         "ChatGPT tutorial":      {"videos": [...], "keyword": "..."},
         ...
       }

   Save that object to `data/youtube_outliers_raw.json`.

2. Run:

       poetry run python scripts/refresh_youtube_outliers.py

Output: `data/youtube_outliers.json` (deduped by video_id, ranked by
`outlier_multiple` descending, capped at top 30).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.fetch.youtube_outliers import (  # noqa: E402 (path shim first)
    build_outliers_cache,
    parse_outliers_response,
)

RAW_PATH = ROOT / "data" / "youtube_outliers_raw.json"
OUT_PATH = ROOT / "data" / "youtube_outliers.json"


def main() -> None:
    if not RAW_PATH.exists():
        print(
            f"missing {RAW_PATH} — collect VidIQ responses first (see this "
            f"file's docstring)",
            file=sys.stderr,
        )
        sys.exit(1)
    raw_by_keyword = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    parsed_per_kw = {
        kw: parse_outliers_response(response)
        for kw, response in raw_by_keyword.items()
    }
    cache = build_outliers_cache(parsed_per_kw)
    OUT_PATH.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {OUT_PATH} ({len(cache['outliers'])} outliers from "
        f"{len(parsed_per_kw)} keywords)"
    )


if __name__ == "__main__":
    main()
