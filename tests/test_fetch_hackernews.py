"""TDD for pipeline.fetch.hackernews against cached Algolia + item fixtures."""

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.fetch import hackernews
from pipeline.fetch.hackernews import HNPost

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEARCH_FIXTURE = FIXTURES / "hn_sample.json"
ITEM_FIXTURE = FIXTURES / "hn_items_48073246.json"


def test_parse_search_response_returns_at_least_thirty_posts() -> None:
    posts = hackernews.parse_search_response(json.loads(SEARCH_FIXTURE.read_text()))
    assert len(posts) >= 30


def test_search_posts_have_positive_points() -> None:
    posts = hackernews.parse_search_response(json.loads(SEARCH_FIXTURE.read_text()))
    assert all(p.points > 0 for p in posts), "expected all sample posts to have points > 0"


def test_search_posts_have_required_fields() -> None:
    posts = hackernews.parse_search_response(json.loads(SEARCH_FIXTURE.read_text()))
    for p in posts:
        assert p.id > 0
        assert p.title.strip()
        assert p.author.strip()
        assert p.num_comments >= 0
        assert p.created_at.tzinfo is not None


def test_search_posts_default_no_comments_loaded() -> None:
    posts = hackernews.parse_search_response(json.loads(SEARCH_FIXTURE.read_text()))
    assert all(p.comments is None for p in posts), "search response shouldn't include comments"


def test_parse_item_tree_extracts_top_level_comments() -> None:
    tree = json.loads(ITEM_FIXTURE.read_text())
    comments = hackernews.parse_item_tree(tree, limit=10)
    assert 1 <= len(comments) <= 10
    for c in comments:
        assert c.id > 0
        assert c.author.strip()
        assert c.created_at.tzinfo is not None


def test_attach_comments_populates_field() -> None:
    posts = hackernews.parse_search_response(json.loads(SEARCH_FIXTURE.read_text()))
    target = next(p for p in posts if p.id == 48073246)
    tree = json.loads(ITEM_FIXTURE.read_text())
    comments = hackernews.parse_item_tree(tree, limit=5)
    target = hackernews.attach_comments(target, comments)
    assert target.comments is not None
    assert len(target.comments) == len(comments)


# v0.2.0 — four-pass strategy tests


def _make_post(*, id: int, title: str, story_text: str = "", points: int = 50) -> HNPost:
    return HNPost(
        id=id,
        title=title,
        url="https://example.com/" + str(id),
        points=points,
        num_comments=0,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        story_text=story_text or None,
        author="someone",
    )


def test_is_ai_relevant_rejects_hardware_noise() -> None:
    """RTX 5090 / Apple Silicon stories should not pass the front_page AI filter."""
    post = _make_post(
        id=99001,
        title="RTX 5090 and M4 MacBook Air: Can It Game?",
        points=653,
    )
    assert hackernews._is_ai_relevant(post) is False


def test_is_ai_relevant_accepts_llm_story() -> None:
    """Stories containing LLM/Anthropic/GPT/etc. should pass the front_page filter."""
    post = _make_post(
        id=99002,
        title="LLMs corrupt your documents when you delegate",
        points=478,
    )
    assert hackernews._is_ai_relevant(post) is True


def test_fetch_deduplication_across_passes(monkeypatch) -> None:
    """Same post returned by both the keyword pass and the Show HN pass
    must appear exactly once in the merged result.
    """
    duplicate = _make_post(
        id=12345,
        title="Show HN: My LLM agent framework",
        points=80,
    )

    monkeypatch.setattr(
        hackernews, "_safe_search",
        lambda client, kw, cutoff, *, min_points: [duplicate],
    )
    monkeypatch.setattr(
        hackernews, "_safe_search_by_tag",
        lambda client, tags, cutoff, *, min_points: [duplicate],
    )
    monkeypatch.setattr(hackernews, "_fetch_item", lambda client, item_id: {"children": []})
    monkeypatch.setattr(hackernews.time, "sleep", lambda _: None)

    posts = hackernews.fetch_ai_posts(lookback_days=1, keywords=["LLM"], hydrate_top_n=0)
    ids = [p.id for p in posts]
    assert ids.count(12345) == 1, f"expected one copy of post 12345, got {ids}"
