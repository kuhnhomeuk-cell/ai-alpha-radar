"""TDD for pipeline.batch_cache (audit 4.2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.batch_cache import BatchCache, hash_requests


def _req(cid: str, prompt: str) -> dict:
    return {
        "custom_id": cid,
        "params": {"messages": [{"role": "user", "content": prompt}]},
    }


def test_hash_requests_is_deterministic_across_order() -> None:
    a = [_req("a_0", "P1"), _req("b_0", "P2")]
    b = [_req("b_0", "P2"), _req("a_0", "P1")]
    assert hash_requests(a) == hash_requests(b)


def test_hash_requests_differs_on_prompt_change() -> None:
    a = [_req("a_0", "P1")]
    b = [_req("a_0", "P1-changed")]
    assert hash_requests(a) != hash_requests(b)


def test_cache_returns_none_for_unknown_key(tmp_path: Path) -> None:
    cache = BatchCache(tmp_path / "x.json")
    assert cache.get("nope") is None


def test_cache_round_trip_batch_id(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    cache = BatchCache(p)
    cache.set_batch_id("k1", "batch_abc")
    # Persisted to disk
    assert "batch_abc" in p.read_text()
    # Re-opened instance sees the entry
    cache2 = BatchCache(p)
    entry = cache2.get("k1")
    assert entry is not None and entry["batch_id"] == "batch_abc"
    assert "results" not in entry


def test_cache_set_results_overwrites_batch_id_entry(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    cache = BatchCache(p)
    cache.set_batch_id("k1", "batch_abc")
    cache.set_results("k1", "batch_abc", {"a_0": {"summary": "x"}})
    entry = BatchCache(p).get("k1")
    assert entry is not None
    assert entry["results"] == {"a_0": {"summary": "x"}}


def test_cache_evicts_entries_older_than_ttl(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=25)).isoformat()
    p.write_text(json.dumps({"stale": {"submitted_at": old_ts, "batch_id": "x"}}))
    cache = BatchCache(p)
    assert cache.get("stale") is None
    # And the eviction is persisted
    assert "stale" not in p.read_text()


def test_cache_keeps_entries_within_ttl(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    fresh_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    p.write_text(json.dumps({"fresh": {"submitted_at": fresh_ts, "batch_id": "x"}}))
    cache = BatchCache(p)
    assert cache.get("fresh") is not None


def test_cache_tolerates_corrupt_file(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text("{ not json")
    cache = BatchCache(p)  # should NOT raise
    assert cache.get("anything") is None
    cache.set_batch_id("ok", "batch_y")
    assert BatchCache(p).get("ok") is not None
