"""TDD for pipeline.fetch.hackernews against cached Algolia + item fixtures."""

import json
from pathlib import Path

from pipeline.fetch import hackernews

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
