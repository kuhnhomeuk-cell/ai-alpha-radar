"""TDD for pipeline.fetch.bluesky — Jetstream parse + SQLite store (audit 3.6)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline.fetch import bluesky


def _make_event(text: str, handle: str = "user.bsky.social", ts: str = "2026-05-13T12:00:00.000Z"):
    return {
        "did": "did:plc:abc",
        "time_us": int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1_000_000),
        "kind": "commit",
        "commit": {
            "rev": "fake",
            "operation": "create",
            "collection": "app.bsky.feed.post",
            "rkey": "fake",
            "record": {
                "$type": "app.bsky.feed.post",
                "createdAt": ts,
                "text": text,
                "langs": ["en"],
            },
        },
        # Optional convenience field — production events carry handle in didcache
        "_handle": handle,
    }


def test_parse_post_event_extracts_text() -> None:
    event = _make_event("hello world from bluesky")
    mention = bluesky.parse_post_event(event)
    assert mention is not None
    assert "hello world" in mention.text
    assert mention.handle == "user.bsky.social"


def test_parse_post_event_skips_non_post_records() -> None:
    event = _make_event("x")
    event["commit"]["collection"] = "app.bsky.feed.like"
    assert bluesky.parse_post_event(event) is None


def test_parse_post_event_skips_deletes() -> None:
    event = _make_event("x")
    event["commit"]["operation"] = "delete"
    assert bluesky.parse_post_event(event) is None


def test_keyword_filter_matches_substring_case_insensitive() -> None:
    event = _make_event("I love LLMs and tooling")
    assert bluesky.matches_keyword(
        bluesky.parse_post_event(event), keywords={"llm", "claude"}
    )


def test_keyword_filter_rejects_non_match() -> None:
    event = _make_event("breakfast was good")
    assert not bluesky.matches_keyword(
        bluesky.parse_post_event(event), keywords={"llm", "claude"}
    )


def test_mention_store_roundtrip(tmp_path: Path) -> None:
    store = bluesky.MentionStore(tmp_path / "mentions.sqlite")
    store.write_many(
        [
            bluesky.BlueskyMention(
                handle="a", text="llm news today", created_at=datetime(2026, 5, 13, tzinfo=timezone.utc)
            ),
            bluesky.BlueskyMention(
                handle="b", text="claude is great", created_at=datetime(2026, 5, 12, tzinfo=timezone.utc)
            ),
        ]
    )
    counts = store.mention_counts_per_keyword(
        keywords={"llm", "claude"},
        since=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert counts["llm"] == 1
    assert counts["claude"] == 1


def test_mention_store_since_excludes_old_rows(tmp_path: Path) -> None:
    store = bluesky.MentionStore(tmp_path / "mentions.sqlite")
    store.write_many(
        [
            bluesky.BlueskyMention(
                handle="a", text="llm news", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
            ),
        ]
    )
    counts = store.mention_counts_per_keyword(
        keywords={"llm"}, since=datetime(2026, 5, 1, tzinfo=timezone.utc)
    )
    assert counts.get("llm", 0) == 0


def test_mention_store_missing_db_returns_zero_counts(tmp_path: Path) -> None:
    counts = bluesky.read_mention_counts(
        tmp_path / "nope.sqlite",
        keywords={"llm"},
        since=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert counts == {}
