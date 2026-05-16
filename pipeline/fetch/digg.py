"""Fetch Digg AI top stories as a cross-reference signal.

Digg (digg.com/ai, relaunched May 2026 by Kevin Rose + Alexis Ohanian) is an
algorithmic news aggregator that ranks AI stories by engagement velocity from
~2,000 curated X (Twitter) AI influencers. There's no Digg upvote system — the
"crowd" is a fixed graph of X accounts (Altman, Karpathy, LeCun, etc.).

We use it as a cross-reference signal layer:
- When a topic appears in HN/arxiv AND Digg → boost consensus_ratio.
- When it appears on Digg but not HN → flag as 'media-driven' / X-bubble.
- When it appears on HN but not Digg → flag as 'technical / practitioner'.

Access: Firecrawl /v2/scrape with `json` format (no Digg API exists).
Cache: data/digg_ai_corpus.json — cumulative, keyed by Digg story slug.
Each story accumulates observations[] over time so rank trajectory is preserved
for later velocity analysis.

Failure mode: Digg is a supplemental signal. If the live fetch fails (Firecrawl
down, key missing, layout change), the fetcher returns [] silently and the
pipeline falls back to the cached corpus via load_recent_corpus_stories().
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

FIRECRAWL_API = "https://api.firecrawl.dev/v2/scrape"
DIGG_AI_URL = "https://digg.com/ai"
CORPUS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "digg_ai_corpus.json"
)
DIGG_USER_AGENT = (
    "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"
)

# JSON schema we hand to Firecrawl for structured extraction. Keep the schema
# loose (only rank/title/story_url required) — Firecrawl drops items that
# don't satisfy `required`, and Digg's UI sometimes omits the excerpt or
# timestamp on a card.
_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "snapshot_date": {"type": "string"},
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "title": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "views": {"type": "string"},
                    "timestamp_relative": {"type": "string"},
                    "story_url": {"type": "string"},
                },
                "required": ["rank", "title", "story_url"],
            },
        },
    },
}

_EXTRACT_PROMPT = (
    "Extract the top 30 ranked AI news stories from the numbered list on this "
    "page. Skip the four Highlights cards at the top (most viewed, rising "
    "discussion, fastest-climbing, in case you missed it). For each story: "
    "rank (integer 1-30), title, excerpt blurb (the short paragraph under the "
    "title), views (engagement number like '55.1k' or '120,967' — keep as raw "
    "string), timestamp_relative ('1h' / '14h' / '2d'), and story_url (the "
    "/ai/{slug} link, with or without ?rank query). Also capture the date "
    "label visible at the top of the ranked list."
)

# Schema for historical/multi-day pulls. Each section (today, yesterday,
# day-before) has its own date label and its own numbered list. We capture
# them as separate sections so observations get the correct snapshot_date.
_HISTORICAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Section heading, e.g. 'Today' or 'Yesterday's Top Stories'"},
                    "date": {"type": "string", "description": "Date label, e.g. 'May 16, 2026'"},
                    "stories": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "rank": {"type": "integer"},
                                "title": {"type": "string"},
                                "excerpt": {"type": "string"},
                                "views": {"type": "string"},
                                "timestamp_relative": {"type": "string"},
                                "story_url": {"type": "string"},
                            },
                            "required": ["rank", "title", "story_url"],
                        },
                    },
                },
                "required": ["stories"],
            },
        },
    },
    "required": ["sections"],
}

_HISTORICAL_PROMPT = (
    "Extract ALL ranked AI story sections visible on this page after multiple "
    "scrolls. The page typically shows: (a) 'Today' / current top 30 stories, "
    "then (b) 'Yesterday's Top Stories, {date}' with another ~30 stories, then "
    "potentially earlier dates. For EACH section: the section label, the date "
    "shown (e.g. 'May 16, 2026'), and the numbered list of stories below it. "
    "Each story has rank, title, excerpt, views, timestamp_relative, and "
    "story_url. Skip the four Highlights cards at the very top. Return sections "
    "in the order they appear on the page (most recent first)."
)


class DiggAIStory(BaseModel):
    rank: int
    title: str
    excerpt: str = ""
    views: str = ""
    timestamp_relative: str = ""
    story_url: str  # canonical: "/ai/{slug}" without query string
    story_id: str   # slug only
    snapshot_date: str = ""  # human-readable, e.g. "Saturday, May 16th, 2026"
    fetched_at: datetime


def _slug_from_url(url: str) -> str:
    """Extract just the story id slug from '/ai/{slug}?rank=N' or '/ai/{slug}'."""
    if "/ai/" not in url:
        return ""
    return url.split("/ai/")[-1].split("?")[0].strip()


@with_retry(attempts=3, base_delay=2.0, max_delay=15.0)
def _firecrawl_scrape(api_key: str) -> dict[str, Any]:
    """Call Firecrawl /v2/scrape with structured JSON output."""
    payload = {
        "url": DIGG_AI_URL,
        "formats": [
            {"type": "json", "schema": _EXTRACT_SCHEMA, "prompt": _EXTRACT_PROMPT}
        ],
        "onlyMainContent": True,
        "waitFor": 5000,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": DIGG_USER_AGENT,
    }
    with httpx.Client(timeout=120, headers=headers) as client:
        r = client.post(FIRECRAWL_API, json=payload)
        r.raise_for_status()
        return r.json()


def parse_firecrawl_response(resp: dict[str, Any]) -> list[DiggAIStory]:
    """Parse a Firecrawl scrape response into DiggAIStory list. Pure function
    so tests can hit it with a fixture payload.
    """
    data = (resp or {}).get("data") or {}
    json_block = data.get("json") or {}
    raw_stories = json_block.get("stories") or []
    snapshot_date = json_block.get("snapshot_date") or ""
    now = datetime.now(tz=timezone.utc)
    out: list[DiggAIStory] = []
    for s in raw_stories:
        story_url = (s.get("story_url") or "").strip()
        slug = _slug_from_url(story_url)
        title = (s.get("title") or "").strip()
        raw_rank = s.get("rank")
        # A real ranked story must have a positive integer rank — drop rank=0
        # / missing-rank entries (which are usually mis-extracted highlight
        # cards or section dividers).
        try:
            rank = int(raw_rank) if raw_rank is not None else 0
        except (TypeError, ValueError):
            rank = 0
        if not slug or not title or rank <= 0:
            continue
        try:
            out.append(
                DiggAIStory(
                    rank=rank,
                    title=title,
                    excerpt=(s.get("excerpt") or "").strip(),
                    views=str(s.get("views") or ""),
                    timestamp_relative=str(s.get("timestamp_relative") or ""),
                    story_url=story_url.split("?")[0],
                    story_id=slug,
                    snapshot_date=snapshot_date,
                    fetched_at=now,
                )
            )
        except Exception:
            continue
    return out


def fetch_digg_ai_stories(api_key: Optional[str] = None) -> list[DiggAIStory]:
    """Live fetch of Digg AI's top ~30 stories via Firecrawl.

    Returns empty list on any failure — Digg is a supplemental cross-reference
    signal and must not park the pipeline. Caller should fall back to the
    cached corpus via load_recent_corpus_stories().
    """
    key = api_key or os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return []
    try:
        resp = _firecrawl_scrape(key)
    except Exception:
        return []
    return parse_firecrawl_response(resp)


@with_retry(attempts=2, base_delay=3.0, max_delay=20.0)
def _firecrawl_scrape_historical(api_key: str, scroll_count: int = 6) -> dict[str, Any]:
    """Firecrawl /v2/scrape with scroll actions, structured-extract historical
    sections. Returns parsed JSON response.

    scroll_count: number of "scroll to bottom" passes. Each pass advances the
    virtualized list by ~one viewport. 6 scrolls reliably reveals Yesterday's
    Top Stories section; more scrolls reach 2-day-old data when present.
    """
    actions: list[dict[str, Any]] = [{"type": "wait", "milliseconds": 4000}]
    for _ in range(scroll_count):
        actions.append({"type": "scroll", "direction": "down"})
        actions.append({"type": "wait", "milliseconds": 2500})
    payload = {
        "url": DIGG_AI_URL,
        "actions": actions,
        "formats": [
            {
                "type": "json",
                "schema": _HISTORICAL_SCHEMA,
                "prompt": _HISTORICAL_PROMPT,
            }
        ],
        "onlyMainContent": True,
        "waitFor": 4000,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": DIGG_USER_AGENT,
    }
    with httpx.Client(timeout=240, headers=headers) as client:
        r = client.post(FIRECRAWL_API, json=payload)
        r.raise_for_status()
        return r.json()


def parse_historical_response(resp: dict[str, Any]) -> list[DiggAIStory]:
    """Parse a multi-section historical response into a flat list of stories.

    Each section gets its own snapshot_date so corpus observations preserve the
    correct day attribution.
    """
    data = (resp or {}).get("data") or {}
    json_block = data.get("json") or {}
    sections = json_block.get("sections") or []
    now = datetime.now(tz=timezone.utc)
    out: list[DiggAIStory] = []
    for sec in sections:
        date_label = (sec.get("date") or sec.get("label") or "").strip()
        for s in sec.get("stories") or []:
            story_url = (s.get("story_url") or "").strip()
            slug = _slug_from_url(story_url)
            title = (s.get("title") or "").strip()
            raw_rank = s.get("rank")
            try:
                rank = int(raw_rank) if raw_rank is not None else 0
            except (TypeError, ValueError):
                rank = 0
            if not slug or not title or rank <= 0:
                continue
            try:
                out.append(
                    DiggAIStory(
                        rank=rank,
                        title=title,
                        excerpt=(s.get("excerpt") or "").strip(),
                        views=str(s.get("views") or ""),
                        timestamp_relative=str(s.get("timestamp_relative") or ""),
                        story_url=story_url.split("?")[0],
                        story_id=slug,
                        snapshot_date=date_label,
                        fetched_at=now,
                    )
                )
            except Exception:
                continue
    return out


def fetch_digg_historical_stories(
    scroll_count: int = 6, api_key: Optional[str] = None
) -> list[DiggAIStory]:
    """One-shot backfill: scroll Digg /ai to load multi-day sections, then
    extract stories from each. Returns [] on any failure.

    Use this once for initial corpus seeding, or weekly to recover from
    fetcher outages. Day-to-day daily refresh uses fetch_digg_ai_stories()
    instead — cheaper, more reliable.
    """
    key = api_key or os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return []
    try:
        resp = _firecrawl_scrape_historical(key, scroll_count=scroll_count)
    except Exception:
        return []
    return parse_historical_response(resp)


def load_corpus(path: Path = CORPUS_PATH) -> dict[str, Any]:
    """Read the cumulative corpus from disk. Empty shell if missing."""
    if not path.exists():
        return {"created_at": "", "stories": {}, "last_refresh": "", "story_count": 0}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"created_at": "", "stories": {}, "last_refresh": "", "story_count": 0}


def update_corpus(
    stories: list[DiggAIStory], path: Path = CORPUS_PATH
) -> dict[str, Any]:
    """Merge today's stories into the cumulative corpus.

    Storage shape (matches the existing data/digg_ai_corpus.json layout):
      {
        "created_at": <first-ever fetch ts>,
        "last_refresh": <most recent fetch ts>,
        "story_count": <unique stories>,
        "stories": {
          "<slug>": {
            "story_id", "title", "excerpt", "story_url",
            "first_seen", "last_seen",
            "observations": [{ts, rank, views, timestamp_relative, snapshot_date}, ...]
          }
        }
      }

    If today's fetch returned no stories, the corpus is left untouched so
    we don't lose history on a transient Firecrawl outage.
    """
    if not stories:
        return load_corpus(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus(path)
    fetched_at_iso = stories[0].fetched_at.isoformat()
    if not corpus.get("created_at"):
        corpus["created_at"] = fetched_at_iso
    for s in stories:
        entry = corpus["stories"].get(s.story_id)
        if entry is None:
            entry = {
                "story_id": s.story_id,
                "title": s.title,
                "excerpt": s.excerpt,
                "story_url": s.story_url,
                "first_seen": fetched_at_iso,
                "last_seen": fetched_at_iso,
                "observations": [],
            }
            corpus["stories"][s.story_id] = entry
        entry["last_seen"] = fetched_at_iso
        entry["observations"].append(
            {
                "ts": fetched_at_iso,
                "rank": s.rank,
                "views": s.views,
                "timestamp_relative": s.timestamp_relative,
                "snapshot_date": s.snapshot_date,
            }
        )
    corpus["last_refresh"] = fetched_at_iso
    corpus["story_count"] = len(corpus["stories"])
    path.write_text(json.dumps(corpus, indent=2))
    return corpus


def load_recent_corpus_stories(
    lookback_days: int = 7, path: Path = CORPUS_PATH
) -> list[dict[str, Any]]:
    """Return corpus stories observed within the last N days.

    This is what run.py consumes for per-topic substring matching — uses the
    cached corpus (not a live fetch) so the consensus signal works even if
    today's Firecrawl call failed.
    """
    corpus = load_corpus(path)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    out: list[dict[str, Any]] = []
    for entry in corpus.get("stories", {}).values():
        last = entry.get("last_seen", "")
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if last_dt >= cutoff:
            out.append(entry)
    return out


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv(
        Path(__file__).resolve().parent.parent.parent / ".env.local",
        override=False,
    )
    stories = fetch_digg_ai_stories()
    if not stories:
        print(
            "⚠ No stories fetched. Check FIRECRAWL_API_KEY in .env.local "
            "and that digg.com/ai is reachable.",
            file=sys.stderr,
        )
        sys.exit(1)
    corpus = update_corpus(stories)
    print(f"✓ Fetched {len(stories)} Digg AI stories")
    print(f"✓ Corpus now has {corpus['story_count']} unique stories total")
    print(f"  → {CORPUS_PATH}")
    for s in sorted(stories, key=lambda x: x.rank)[:5]:
        print(f"  #{s.rank:>2} {s.title[:80]}")
