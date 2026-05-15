"""TDD for pipeline.fetch.hackernews against cached Algolia + item fixtures."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, hackernews

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEARCH_FIXTURE = FIXTURES / "hn_sample.json"
ITEM_FIXTURE = FIXTURES / "hn_items_48073246.json"


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(hackernews.time, "sleep", lambda _s: None)


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


# ---------- audit 4.5 — HTTP-layer integration tests ----------


def _search_payload() -> dict:
    return json.loads(SEARCH_FIXTURE.read_text())


@respx.mock
def test_fetch_ai_posts_200_path_dedupes_across_keywords(_no_sleep: None) -> None:
    # Same payload served for every keyword: dedupe by id must collapse them.
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=_search_payload())
    )
    respx.get(url__regex=r"https://hn\.algolia\.com/api/v1/items/\d+").mock(
        return_value=httpx.Response(200, json=json.loads(ITEM_FIXTURE.read_text()))
    )
    posts = hackernews.fetch_ai_posts(lookback_days=365, keywords=["AI", "LLM"], hydrate_top_n=1)
    # Sample fixture has unique objectIDs; calling twice still produces the same set.
    assert len(posts) >= 30


@respx.mock
def test_fetch_ai_posts_honors_retry_after_then_succeeds(_no_sleep: None) -> None:
    route = respx.get("https://hn.algolia.com/api/v1/search").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_search_payload()),
        ]
    )
    hackernews.fetch_ai_posts(lookback_days=365, keywords=["AI"], hydrate_top_n=0)
    assert route.call_count == 2


@respx.mock
def test_fetch_ai_posts_500_exhausts_and_raises(_no_sleep: None) -> None:
    route = respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        hackernews.fetch_ai_posts(lookback_days=365, keywords=["AI"], hydrate_top_n=0)
    assert route.call_count == 3


@respx.mock
def test_fetch_ai_posts_malformed_body_returns_empty(_no_sleep: None) -> None:
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json={"unexpected": []})
    )
    posts = hackernews.fetch_ai_posts(lookback_days=365, keywords=["AI"], hydrate_top_n=0)
    assert posts == []
