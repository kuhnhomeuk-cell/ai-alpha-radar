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


def test_keyword_filter_matches_word_start() -> None:
    """Plurals and English compounds still resolve — 'llm' matches 'LLMs'
    because the regex uses a word-START boundary, not both-sided."""
    event = _make_event("I love LLMs and tooling")
    assert bluesky.matches_keyword(
        bluesky.parse_post_event(event), keywords={"llm", "claude"}
    )


def test_keyword_filter_rejects_non_match() -> None:
    event = _make_event("breakfast was good")
    assert not bluesky.matches_keyword(
        bluesky.parse_post_event(event), keywords={"llm", "claude"}
    )


def test_keyword_filter_rejects_substring_inside_unrelated_word() -> None:
    """The pre-2026 substring matcher matched 'mcp' inside any 3-char
    sequence and 'agent' inside Portuguese 'agente' — the SQLite cache
    accumulated thousands of multilingual non-AI posts as a result.
    Word-start boundary fixes the trivial substring collisions; the
    lang=en filter (applied at subscribe time) handles the rest."""
    # "mcp" should NOT match an unrelated sequence like "company-mcp-xyz".
    e1 = _make_event("the company-mcp-xyz product reveal")
    # ^^ "mcp" preceded by "-" so word boundary fires => still matches.
    # Better example: "mcp" inside a longer alphanumeric token.
    e2 = _make_event("the campmcpground was lovely")
    assert not bluesky.matches_keyword(
        bluesky.parse_post_event(e2), keywords={"mcp"}
    )


def test_event_is_english_when_langs_contains_en() -> None:
    event = _make_event("hello")  # default has langs=["en"]
    assert bluesky.event_is_english(event)


def test_event_is_english_false_for_non_english() -> None:
    event = _make_event("ボンジュール")
    event["commit"]["record"]["langs"] = ["ja"]
    assert not bluesky.event_is_english(event)


def test_event_is_english_true_when_langs_missing() -> None:
    """Some clients omit langs entirely. Default to permitting through
    rather than blocking — the keyword filter still has to fire."""
    event = _make_event("hello world")
    del event["commit"]["record"]["langs"]
    assert bluesky.event_is_english(event)


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


def test_parse_post_event_clamps_pre_bluesky_dates() -> None:
    """AT Protocol does NOT validate createdAt against wall clock, so users
    can backdate posts. One row in our cache was dated 2013-09-06 — before
    Bluesky existed. Clamp anything earlier than the network launch to now()
    so the date-range query stays honest."""
    event = _make_event("legit post", ts="2013-09-06T00:00:00.000Z")
    mention = bluesky.parse_post_event(event)
    assert mention is not None
    assert mention.created_at >= datetime(2023, 1, 1, tzinfo=timezone.utc)


def test_parse_post_event_falls_back_to_did_when_handle_missing() -> None:
    """Jetstream events carry `did` at the top level; the human-readable
    handle requires a separate PLC directory call. Until that hydration is
    wired, store the did so the row isn't anonymous."""
    event = _make_event("post text")
    event.pop("_handle")  # production: not hydrated
    mention = bluesky.parse_post_event(event)
    assert mention is not None
    assert mention.handle == "did:plc:abc"


def test_parse_post_event_handle_still_preferred_when_present() -> None:
    """If a future subscriber DOES hydrate _handle, we keep it instead of the did."""
    event = _make_event("post", handle="cool.user.bsky.social")
    mention = bluesky.parse_post_event(event)
    assert mention is not None
    assert mention.handle == "cool.user.bsky.social"


def test_mention_store_missing_db_returns_zero_counts(tmp_path: Path) -> None:
    counts = bluesky.read_mention_counts(
        tmp_path / "nope.sqlite",
        keywords={"llm"},
        since=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    assert counts == {}
