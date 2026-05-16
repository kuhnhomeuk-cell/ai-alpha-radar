"""TDD for pipeline.fetch.digg — Digg AI cross-reference fetcher."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline.fetch import digg

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_sample() -> dict:
    return json.loads((FIXTURES / "digg_firecrawl_sample.json").read_text())


def test_parse_firecrawl_response_extracts_valid_stories() -> None:
    """Parses 3 valid stories; drops the 3 malformed entries (missing rank /
    missing title / missing usable url)."""
    resp = _load_sample()
    stories = digg.parse_firecrawl_response(resp)
    assert len(stories) == 3
    titles = [s.title for s in stories]
    assert "Fei-Fei Li warns AI neglects physical and embodied world" in titles


def test_parse_firecrawl_response_strips_rank_query_from_url() -> None:
    resp = _load_sample()
    stories = digg.parse_firecrawl_response(resp)
    by_id = {s.story_id: s for s in stories}
    # URL had ?rank=2 in fixture; canonical story_url drops it
    assert by_id["ejm0eub7"].story_url == "/ai/ejm0eub7"
    # URL without query still parses
    assert by_id["ol7f3xci"].story_url == "/ai/ol7f3xci"


def test_parse_firecrawl_response_carries_snapshot_date() -> None:
    resp = _load_sample()
    stories = digg.parse_firecrawl_response(resp)
    assert stories[0].snapshot_date == "Saturday, May 16th, 2026"


def test_parse_firecrawl_response_empty_response_returns_empty() -> None:
    assert digg.parse_firecrawl_response({}) == []
    assert digg.parse_firecrawl_response({"data": {}}) == []
    assert digg.parse_firecrawl_response({"data": {"json": {"stories": []}}}) == []


def test_slug_from_url_handles_variants() -> None:
    assert digg._slug_from_url("/ai/abc123?rank=5") == "abc123"
    assert digg._slug_from_url("/ai/abc123") == "abc123"
    assert digg._slug_from_url("https://digg.com/ai/xyz?foo=bar") == "xyz"
    assert digg._slug_from_url("/not-ai/foo") == ""
    assert digg._slug_from_url("") == ""


def test_update_corpus_creates_new_file(tmp_path: Path) -> None:
    """First-ever fetch should produce a corpus with story_count == N."""
    resp = _load_sample()
    stories = digg.parse_firecrawl_response(resp)
    corpus_path = tmp_path / "digg_ai_corpus.json"
    corpus = digg.update_corpus(stories, path=corpus_path)
    assert corpus["story_count"] == 3
    assert corpus_path.exists()
    saved = json.loads(corpus_path.read_text())
    assert set(saved["stories"].keys()) == {"qg9o8ozd", "ejm0eub7", "ol7f3xci"}


def test_update_corpus_appends_observation_on_second_run(tmp_path: Path) -> None:
    """A story re-observed at a different rank gets an additional entry in
    observations[] — that's the rank-trajectory time-series."""
    resp = _load_sample()
    stories = digg.parse_firecrawl_response(resp)
    corpus_path = tmp_path / "digg_ai_corpus.json"
    digg.update_corpus(stories, path=corpus_path)
    # Re-run: same story, but bump fetched_at so it's a separate observation
    later = datetime.now(tz=timezone.utc) + timedelta(hours=6)
    for s in stories:
        s.fetched_at = later
    corpus = digg.update_corpus(stories, path=corpus_path)
    assert corpus["story_count"] == 3  # no new stories
    first = corpus["stories"]["qg9o8ozd"]
    assert len(first["observations"]) == 2  # initial + later
    # first_seen never moves; last_seen advances
    assert first["first_seen"] != first["last_seen"]


def test_update_corpus_with_empty_stories_does_not_touch_file(tmp_path: Path) -> None:
    """If Firecrawl returns nothing, the corpus must stay intact (degraded
    fetch should not lose history)."""
    corpus_path = tmp_path / "digg_ai_corpus.json"
    # Seed the corpus with a known story
    resp = _load_sample()
    digg.update_corpus(digg.parse_firecrawl_response(resp), path=corpus_path)
    before = corpus_path.read_text()
    # Empty fetch: corpus should be untouched on disk
    digg.update_corpus([], path=corpus_path)
    assert corpus_path.read_text() == before


def test_load_recent_corpus_stories_respects_lookback(tmp_path: Path) -> None:
    """Stories last seen more than N days ago must be excluded."""
    corpus_path = tmp_path / "digg_ai_corpus.json"
    resp = _load_sample()
    stories = digg.parse_firecrawl_response(resp)
    # Force one story to be 20 days old, leave the others fresh
    digg.update_corpus(stories, path=corpus_path)
    raw = json.loads(corpus_path.read_text())
    old = (datetime.now(tz=timezone.utc) - timedelta(days=20)).isoformat()
    raw["stories"]["ol7f3xci"]["last_seen"] = old
    corpus_path.write_text(json.dumps(raw))
    # 7-day window: ol7f3xci should be filtered out
    fresh = digg.load_recent_corpus_stories(lookback_days=7, path=corpus_path)
    assert {s["story_id"] for s in fresh} == {"qg9o8ozd", "ejm0eub7"}


def test_fetch_digg_ai_stories_without_key_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No FIRECRAWL_API_KEY → silent empty list (Digg is supplemental)."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert digg.fetch_digg_ai_stories(api_key=None) == []


def test_fetch_digg_ai_stories_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the Firecrawl call raises, fetch returns [] silently."""

    def _boom(api_key: str) -> dict:
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(digg, "_firecrawl_scrape", _boom)
    assert digg.fetch_digg_ai_stories(api_key="fake-key") == []


def _load_historical() -> dict:
    return json.loads((FIXTURES / "digg_historical_sample.json").read_text())


def test_parse_historical_response_extracts_all_sections() -> None:
    """Historical response has 2 sections (today + yesterday). Expect 4 valid
    stories total; the malformed 'missing rank' entry should be dropped."""
    resp = _load_historical()
    stories = digg.parse_historical_response(resp)
    assert len(stories) == 4
    titles = {s.title for s in stories}
    assert "Anthropic resets Claude rate limits for all users" in titles
    assert "X open-sources For You feed recommendation algorithm" in titles


def test_parse_historical_response_attributes_correct_date_per_section() -> None:
    """Each story's snapshot_date should be the date label of its section."""
    resp = _load_historical()
    stories = digg.parse_historical_response(resp)
    by_id = {s.story_id: s for s in stories}
    assert by_id["qg9o8ozd"].snapshot_date == "Saturday, May 16th, 2026"
    assert by_id["k6gv5lnr"].snapshot_date == "May 15, 2026"


def test_parse_historical_response_empty_returns_empty() -> None:
    assert digg.parse_historical_response({}) == []
    assert digg.parse_historical_response({"data": {"json": {"sections": []}}}) == []


def test_fetch_digg_historical_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the historical scroll-scrape raises, fetch returns [] silently."""

    def _boom(api_key: str, scroll_count: int = 6) -> dict:
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(digg, "_firecrawl_scrape_historical", _boom)
    assert digg.fetch_digg_historical_stories(api_key="fake-key") == []
