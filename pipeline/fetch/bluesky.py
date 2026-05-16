"""Bluesky Jetstream firehose accumulator + reader.

Audit item 3.6. Jetstream is a wss:// stream, not a request-response —
running a 5-minute subscribe inside the daily orchestrator would bloat
runtime and miss data outside that window. Per the audit's "option a"
(recommended), this module ships:

1. Pure parse + filter helpers — testable without websockets.
2. A SQLite-backed MentionStore for cross-run persistence.
3. A reader the orchestrator uses to query counts since a cutoff.

The websocket subscriber itself is a standalone CLI:

    python -m pipeline.fetch.bluesky --duration 600

Operator schedules it separately (cron / launchd / GitHub Action). The
daily pipeline only READS from the SQLite — no streaming work in the
orchestrator path.

Endpoint (subscriber): wss://jetstream2.us-east.bsky.network/subscribe
Filter: app.bsky.feed.post `create` operations whose text matches a
curated AI keyword set OR whose handle is in data/bluesky_handles.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional, Set

from pydantic import BaseModel

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "bluesky_mentions.sqlite"
)
DEFAULT_HANDLES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "bluesky_handles.json"
)
DEFAULT_KEYWORDS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "bluesky_keywords.json"
)
DEFAULT_ENDPOINT = "wss://jetstream2.us-east.bsky.network/subscribe"

# AT Protocol clients can supply arbitrary createdAt values — one row in
# the production cache was dated 2013-09-06, ~10 years before Bluesky's
# public launch. Clamp anything earlier than this floor so the 7d window
# query in mention_counts_per_keyword stays meaningful.
BLUESKY_LAUNCH_FLOOR = datetime(2023, 1, 1, tzinfo=timezone.utc)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT,
    text TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mentions_created_at ON mentions(created_at);
"""


class BlueskyMention(BaseModel):
    handle: Optional[str] = None
    text: str
    created_at: datetime


def parse_post_event(event: dict[str, Any]) -> Optional[BlueskyMention]:
    """Parse a Jetstream event dict into a BlueskyMention, or None to skip.

    Filters: only `app.bsky.feed.post` records on `create` operations.
    """
    commit = event.get("commit") or {}
    if commit.get("collection") != "app.bsky.feed.post":
        return None
    if commit.get("operation") != "create":
        return None
    record = commit.get("record") or {}
    text = record.get("text")
    if not text:
        return None
    created_at_iso = record.get("createdAt") or ""
    try:
        created_at = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
    except ValueError:
        created_at = datetime.now(tz=timezone.utc)
    if created_at < BLUESKY_LAUNCH_FLOOR:
        created_at = datetime.now(tz=timezone.utc)
    # Prefer the human-readable handle when a hydrated subscriber supplies
    # one, but never store an empty string — fall back to the top-level
    # `did` field (always present on Jetstream events). The did keeps rows
    # de-anonymized until a PLC-directory hydration pass is wired.
    handle = event.get("_handle") or event.get("did")
    return BlueskyMention(handle=handle, text=text, created_at=created_at)


@lru_cache(maxsize=256)
def _keyword_regex(kw: str) -> re.Pattern[str]:
    # Word-START boundary only — English plurals and compounds still match
    # ('llm' → 'llms', 'llmops'). The closing boundary is intentionally
    # omitted: requiring `\b{kw}\b` would drop the common plural cases the
    # existing keyword list ('llm', 'agent') depends on. Foreign-language
    # collisions (Portuguese 'agente', "campmcpground") are blocked by
    # the new word-start boundary AND further by the lang=en filter
    # applied at subscribe time.
    return re.compile(r"\b" + re.escape(kw.lower()), re.IGNORECASE)


def matches_keyword(
    mention: Optional[BlueskyMention], *, keywords: Set[str]
) -> bool:
    if mention is None:
        return False
    text = mention.text
    return any(_keyword_regex(kw).search(text) for kw in keywords)


def event_is_english(event: dict[str, Any]) -> bool:
    """Permit the post through if its record.langs is unset OR includes
    an English variant. Jetstream events whose langs is explicitly
    non-English (`["ja"]`, `["sk"]`, etc.) are filtered out — those drove
    the multilingual noise in the production cache.
    """
    record = (event.get("commit") or {}).get("record") or {}
    langs = record.get("langs")
    if not langs:
        return True
    return any(isinstance(l, str) and l.lower().startswith("en") for l in langs)


class MentionStore:
    """SQLite-backed accumulator. Used by both subscriber + reader paths."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.executescript(_CREATE_TABLE_SQL)

    def write_many(self, mentions: Iterable[BlueskyMention]) -> int:
        rows = [
            (m.handle, m.text, m.created_at.isoformat())
            for m in mentions
        ]
        if not rows:
            return 0
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                "INSERT INTO mentions(handle, text, created_at) VALUES (?, ?, ?)",
                rows,
            )
        return len(rows)

    def mention_counts_per_keyword(
        self, *, keywords: Set[str], since: datetime
    ) -> dict[str, int]:
        """For each keyword, count posts whose text contains it as a
        word-start match (consistent with matches_keyword)."""
        out: dict[str, int] = {kw: 0 for kw in keywords}
        cutoff = since.isoformat()
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT text FROM mentions WHERE created_at >= ?",
                (cutoff,),
            ).fetchall()
        patterns = {kw: _keyword_regex(kw) for kw in keywords}
        for (text,) in rows:
            t = text or ""
            for kw, pat in patterns.items():
                if pat.search(t):
                    out[kw] += 1
        return out


def read_mention_counts(
    db_path: Path, *, keywords: Set[str], since: datetime
) -> dict[str, int]:
    """Reader the orchestrator uses. Returns {} when the DB doesn't exist yet."""
    if not db_path.exists():
        return {}
    return MentionStore(db_path).mention_counts_per_keyword(
        keywords=keywords, since=since
    )


def _load_json_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


# --------- standalone subscriber (operator runs this separately) ---------


def _subscribe(
    duration_seconds: int,
    *,
    db_path: Path,
    keywords: Set[str],
    handles: Set[str],
    endpoint: str = DEFAULT_ENDPOINT,
) -> int:  # pragma: no cover — exercises live websocket
    """Subscribe to Jetstream for `duration_seconds`, persist matching posts."""
    import asyncio

    import websockets  # type: ignore  # optional dep; lazy-imported

    store = MentionStore(db_path)

    async def run() -> int:
        deadline = asyncio.get_event_loop().time() + duration_seconds
        written = 0
        async with websockets.connect(endpoint) as ws:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Drop non-English events at ingestion. The legacy substring
                # matcher captured thousands of multilingual posts (JP
                # baseball, k-drama, Slovak news) where short keywords
                # like 'mcp' or 'rag' collided with unrelated foreign words.
                if not event_is_english(event):
                    continue
                mention = parse_post_event(event)
                if mention is None:
                    continue
                if matches_keyword(mention, keywords=keywords) or (
                    mention.handle and mention.handle in handles
                ):
                    store.write_many([mention])
                    written += 1
        return written

    return asyncio.run(run())


def _cli() -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Bluesky Jetstream subscriber")
    parser.add_argument("--duration", type=int, default=600, help="seconds")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--handles", type=Path, default=DEFAULT_HANDLES_PATH)
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH)
    args = parser.parse_args()

    keywords = set(_load_json_list(args.keywords))
    handles = set(_load_json_list(args.handles))
    written = _subscribe(
        args.duration, db_path=args.db, keywords=keywords, handles=handles
    )
    print(f"persisted {written} bluesky mentions to {args.db}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
