"""TDD for pipeline.fetch.reddit against a cached listing fixture."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from pipeline.fetch import reddit
from pipeline.fetch.reddit import RedditPost

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "reddit_sample.json"


def _posts() -> list[RedditPost]:
    return reddit.parse_listing_response(json.loads(FIXTURE.read_text()))


def test_parse_listing_returns_all_children() -> None:
    posts = _posts()
    assert len(posts) == 3, f"expected 3 posts in fixture, got {len(posts)}"


def test_posts_have_required_fields() -> None:
    for p in _posts():
        assert p.id
        assert p.subreddit
        assert p.title
        assert p.permalink.startswith("https://reddit.com")
        assert p.score >= 0
        assert p.num_comments >= 0
        assert p.created_at.tzinfo is not None


def test_is_self_flag_set_correctly() -> None:
    posts = {p.id: p for p in _posts()}
    assert posts["abc111"].is_self is False
    assert posts["abc222"].is_self is True


def test_is_ai_relevant_matches_ai_terms() -> None:
    post = RedditPost(
        id="x",
        subreddit="singularity",
        title="GPT-5 launched today",
        url="https://example.com",
        permalink="https://reddit.com/r/singularity/comments/x/",
        author="someone",
        selftext="",
        score=500,
        num_comments=100,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert reddit._is_ai_relevant(post) is True


def test_is_ai_relevant_rejects_off_topic() -> None:
    post = RedditPost(
        id="x",
        subreddit="singularity",
        title="Random meme thread about coffee",
        url="https://example.com",
        permalink="https://reddit.com/r/singularity/comments/x/",
        author="someone",
        selftext="just talking about coffee",
        score=500,
        num_comments=100,
        created_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert reddit._is_ai_relevant(post) is False


def test_fetch_ai_posts_applies_score_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """The low-score post (score=8) in the fixture must be filtered out
    because r/MachineLearning has a 50-point threshold.
    """
    payload = json.loads(FIXTURE.read_text())

    def fake_get(self, url, params=None):  # type: ignore[no-untyped-def]
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    monkeypatch.setattr(reddit.time, "sleep", lambda _: None)

    posts = reddit.fetch_ai_posts(subs=["MachineLearning"])
    ids = {p.id for p in posts}
    assert "abc111" in ids  # score=421, passes
    assert "abc222" in ids  # score=156, passes
    assert "low333" not in ids  # score=8, below 50-point threshold


def test_safe_fetch_sub_returns_empty_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(self, url, params=None):  # type: ignore[no-untyped-def]
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(500, request=request)

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    with httpx.Client() as client:
        out = reddit._safe_fetch_sub(client, "MachineLearning", limit=100)
    assert out == []
