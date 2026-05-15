"""Wave 5 — YouTube outliers (operator-scheduled VidIQ MCP source).

Surfaces YouTube videos that are overperforming their channel's baseline,
sourced from VidIQ's `vidiq_outliers` MCP tool, keyed by niche keywords in
`data/youtube_keywords.json`. The result feeds the dashboard's "Comets"
nav route as a card grid.

Architecture mirrors `pipeline/fetch/bluesky.py` (operator-scheduled
subscriber). VidIQ MCP tools are only available inside a Claude Code
session — they can't be called from `python -m pipeline.run` in CI/cron.
So:

1. Pure parse + dedupe + rank helpers — testable, deterministic.
2. A disk-backed cache at `data/youtube_outliers.json` — operator-refreshed.
3. A reader (`fetch_youtube_outliers`) the orchestrator uses.

To refresh the cache, an operator (or Claude Code session) fans out one
`vidiq_outliers(keyword=kw)` call per keyword, runs the result through
`parse_outliers_response` + `build_outliers_cache`, and writes the file
back to `data/youtube_outliers.json`. The daily pipeline only reads.

Cache file schema:

    {
      "refreshed_at": "<ISO-8601>",
      "keywords_queried": [...],
      "outliers": [<YoutubeOutlier model_dump>, ...]
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pipeline.models import YoutubeOutlier

DEFAULT_OUTLIERS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "youtube_outliers.json"
)
DEFAULT_KEYWORDS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "youtube_keywords.json"
)
DEFAULT_TOP_N = 30
MAX_TOPICS_PER_OUTLIER = 8
MAX_TAGS_FROM_VIDEO = 5


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _combine_topics(topics: Any, tags: Any) -> list[str]:
    """Combine videoTopics + first-N videoTags, dedup-preserving order, cap."""
    topics_list = list(topics or [])
    tags_list = list(tags or [])[:MAX_TAGS_FROM_VIDEO]
    out: list[str] = []
    for item in topics_list + tags_list:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in out:
            out.append(item)
        if len(out) >= MAX_TOPICS_PER_OUTLIER:
            break
    return out


def parse_outliers_response(payload: dict[str, Any]) -> list[YoutubeOutlier]:
    """Parse a raw `vidiq_outliers` MCP response into YoutubeOutlier objects.

    Malformed entries are skipped, not raised — a single bad row must not
    crash the whole keyword's batch.
    """
    videos = payload.get("videos") or []
    out: list[YoutubeOutlier] = []
    for v in videos:
        if not isinstance(v, dict):
            continue
        video_id = v.get("videoId")
        title = v.get("videoTitle")
        channel_title = v.get("channelTitle")
        if not (
            isinstance(video_id, str)
            and isinstance(title, str)
            and isinstance(channel_title, str)
            and video_id
            and title
        ):
            continue
        view_count = _coerce_int(v.get("viewCount"))
        breakout = _coerce_float(v.get("breakoutScore"))
        baseline = int(view_count / breakout) if breakout > 0 else 0
        published_ts = _coerce_int(v.get("videoPublishedAt"))
        try:
            published_at = datetime.fromtimestamp(published_ts, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            continue
        thumbnail = v.get("videoThumbnail") or ""
        if not isinstance(thumbnail, str):
            continue
        key_topics = _combine_topics(v.get("videoTopics"), v.get("videoTags"))
        out.append(
            YoutubeOutlier(
                video_id=video_id,
                title=title,
                channel_name=channel_title.strip(),
                view_count=view_count,
                channel_baseline_views=baseline,
                outlier_multiple=breakout,
                published_at=published_at,
                thumbnail_url=thumbnail,
                key_topics=key_topics,
            )
        )
    return out


def dedupe_outliers(outliers: Iterable[YoutubeOutlier]) -> list[YoutubeOutlier]:
    """Per `video_id`, keep the entry with the highest `outlier_multiple`."""
    by_id: dict[str, YoutubeOutlier] = {}
    for o in outliers:
        existing = by_id.get(o.video_id)
        if existing is None or o.outlier_multiple > existing.outlier_multiple:
            by_id[o.video_id] = o
    return list(by_id.values())


def top_n(outliers: Iterable[YoutubeOutlier], *, n: int) -> list[YoutubeOutlier]:
    """Sort by `outlier_multiple` descending and cap at `n`."""
    return sorted(outliers, key=lambda o: o.outlier_multiple, reverse=True)[:n]


def load_keywords(path: Path = DEFAULT_KEYWORDS_PATH) -> list[str]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_outliers_from_disk(
    path: Path = DEFAULT_OUTLIERS_PATH,
) -> list[YoutubeOutlier]:
    """Read the operator-refreshed cache. Missing/malformed → empty list."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    raw = data.get("outliers") or []
    out: list[YoutubeOutlier] = []
    for entry in raw:
        try:
            out.append(YoutubeOutlier.model_validate(entry))
        except Exception:
            continue
    return out


def build_outliers_cache(
    parsed_per_keyword: dict[str, list[YoutubeOutlier]],
    *,
    top_n_cap: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Combine per-keyword parsed lists into the disk-cache structure.

    Order of operations: pool everything, dedupe by `video_id` keeping the
    highest `outlier_multiple`, sort descending, cap at `top_n_cap`, and
    serialize to JSON-mode dicts ready to write to disk.
    """
    pooled: list[YoutubeOutlier] = []
    for outliers in parsed_per_keyword.values():
        pooled.extend(outliers)
    deduped = dedupe_outliers(pooled)
    ranked = top_n(deduped, n=top_n_cap)
    return {
        "refreshed_at": datetime.now(tz=timezone.utc).isoformat(),
        "keywords_queried": list(parsed_per_keyword.keys()),
        "outliers": [o.model_dump(mode="json") for o in ranked],
    }


def fetch_youtube_outliers(
    *, path: Path = DEFAULT_OUTLIERS_PATH
) -> list[YoutubeOutlier]:
    """Public entrypoint used by `pipeline.run`. Reads the disk cache."""
    return load_outliers_from_disk(path)
