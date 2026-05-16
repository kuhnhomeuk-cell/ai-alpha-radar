"""TDD for pipeline.persist — generic write-through corpus cache."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline import persist

NOW = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


def test_update_corpus_creates_new_file_and_keys_by_id(tmp_path: Path) -> None:
    p = tmp_path / "arxiv_corpus.json"
    items = [
        {"id": "2605.00001", "title": "Paper A"},
        {"id": "2605.00002", "title": "Paper B"},
    ]
    n = persist.update_corpus("arxiv", items, id_field="id", now=NOW, path=p)
    assert n == 2
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"2605.00001", "2605.00002"}
    assert data["2605.00001"]["data"]["title"] == "Paper A"
    assert data["2605.00001"]["first_seen"] == NOW.isoformat()
    assert data["2605.00001"]["observations"] == [NOW.isoformat()]


def test_update_corpus_appends_observation_for_repeat(tmp_path: Path) -> None:
    p = tmp_path / "arxiv_corpus.json"
    persist.update_corpus(
        "arxiv", [{"id": "x", "title": "v1"}], id_field="id", now=NOW, path=p
    )
    later = NOW + timedelta(days=1)
    persist.update_corpus(
        "arxiv", [{"id": "x", "title": "v2"}], id_field="id", now=later, path=p
    )
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["x"]["observations"] == [NOW.isoformat(), later.isoformat()]
    assert data["x"]["first_seen"] == NOW.isoformat()
    # Latest data payload wins.
    assert data["x"]["data"]["title"] == "v2"


def test_update_corpus_skips_items_without_id(tmp_path: Path) -> None:
    p = tmp_path / "arxiv_corpus.json"
    items = [{"id": "", "title": "blank"}, {"id": None, "title": "null"}, {"id": "ok", "title": "real"}]
    n = persist.update_corpus("arxiv", items, id_field="id", now=NOW, path=p)
    assert n == 1
    data = json.loads(p.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["ok"]


def test_load_recent_corpus_filters_by_lookback(tmp_path: Path) -> None:
    p = tmp_path / "arxiv_corpus.json"
    # Seed two items at different timestamps
    persist.update_corpus(
        "arxiv", [{"id": "fresh", "title": "F"}], id_field="id",
        now=NOW, path=p,
    )
    persist.update_corpus(
        "arxiv", [{"id": "stale", "title": "S"}], id_field="id",
        now=NOW - timedelta(days=30), path=p,
    )
    recent = persist.load_recent_corpus(
        "arxiv", lookback_days=7, now=NOW, path=p
    )
    titles = {item["title"] for item in recent}
    assert titles == {"F"}


def test_load_recent_corpus_handles_missing_file(tmp_path: Path) -> None:
    out = persist.load_recent_corpus(
        "nope", lookback_days=7, now=NOW, path=tmp_path / "missing.json"
    )
    assert out == []


def test_load_recent_corpus_skips_entries_with_no_observations(
    tmp_path: Path,
) -> None:
    p = tmp_path / "weird_corpus.json"
    p.write_text(
        json.dumps(
            {
                "broken": {"first_seen": NOW.isoformat(), "observations": [], "data": {"x": 1}},
                "fine": {
                    "first_seen": NOW.isoformat(),
                    "observations": [NOW.isoformat()],
                    "data": {"x": 2},
                },
            }
        ),
        encoding="utf-8",
    )
    out = persist.load_recent_corpus("weird", lookback_days=7, now=NOW, path=p)
    assert [item["x"] for item in out] == [2]
