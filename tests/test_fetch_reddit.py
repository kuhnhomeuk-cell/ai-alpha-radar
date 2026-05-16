"""TDD for pipeline.fetch.reddit — public-RSS Reddit fetcher (audit 3.3).

Was PRAW-based before Reddit deprecated new legacy-API script apps in
late 2024; now sources posts via the public per-subreddit RSS endpoint.
score / upvote_ratio / num_comments aren't in the RSS feed, so they
stay at model defaults on every parsed post.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx

from pipeline.fetch import reddit


def _fake_entry(
    *,
    id_str: str = "t3_abc123",
    title: str = "",
    summary: str = "",
    link: str = "",
    published_iso: str = "2026-05-13",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_str,
        title=title,
        summary=summary,
        link=link,
        published_parsed=_time.strptime(published_iso, "%Y-%m-%d"),
    )


def test_parse_rss_entry_extracts_post_id_from_t3_prefix() -> None:
    entry = _fake_entry(id_str="t3_abc123", title="LLM tool launch")
    post = reddit.parse_rss_entry(entry, subreddit="LocalLLaMA")
    assert post.id == "abc123"
    assert post.title == "LLM tool launch"
    assert post.subreddit == "LocalLLaMA"
    # RSS doesn't expose engagement counters — they stay at defaults.
    assert post.score == 0
    assert post.upvote_ratio == 0.5
    assert post.num_comments == 0


def test_parse_rss_entry_strips_html_from_summary() -> None:
    entry = _fake_entry(
        id_str="t3_xyz",
        title="t",
        summary="<p>Some <b>selftext</b> body.</p>",
    )
    post = reddit.parse_rss_entry(entry, subreddit="StableDiffusion")
    assert "<" not in post.selftext
    assert "Some" in post.selftext and "selftext" in post.selftext


def test_parse_rss_entry_falls_back_to_url_id_when_t3_prefix_missing() -> None:
    entry = _fake_entry(
        id_str="https://www.reddit.com/r/LocalLLaMA/comments/zzz999/title/",
        title="t",
    )
    post = reddit.parse_rss_entry(entry, subreddit="LocalLLaMA")
    assert post.id  # non-empty
    assert post.id != entry.id  # parsed something


def test_engagement_score_returns_zero_for_rss_sourced_post() -> None:
    """RSS posts have score=0 and num_comments=0 by default."""
    post = reddit.RedditPost(
        id="x",
        title="t",
        subreddit="s",
        created_at=datetime(2026, 5, 13, 6, tzinfo=timezone.utc),
        url="https://x",
    )
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    assert reddit.engagement_score(post, now=now) == 0.0


def test_dedupe_by_id() -> None:
    base = dict(
        title="t",
        subreddit="s",
        created_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        url="https://x",
        selftext="",
    )
    posts = [
        reddit.RedditPost(id="a", **base),
        reddit.RedditPost(id="a", **base),
        reddit.RedditPost(id="b", **base),
    ]
    deduped = reddit.dedupe_posts(posts)
    assert len(deduped) == 2
    assert {p.id for p in deduped} == {"a", "b"}


def test_top_subreddit_per_term() -> None:
    posts = [
        reddit.RedditPost(
            id=str(i),
            title="LLM ftw" if i < 3 else "Stable Diffusion goes brrr",
            subreddit="LocalLLaMA" if i < 3 else "StableDiffusion",
            created_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            url="https://x",
            selftext="",
        )
        for i in range(5)
    ]
    top = reddit.top_subreddit_per_term(posts, terms=["llm", "diffusion"])
    assert top["llm"] == "LocalLLaMA"
    assert top["diffusion"] == "StableDiffusion"


def test_reddit_mentions_per_term() -> None:
    posts = [
        reddit.RedditPost(
            id=str(i),
            title="LLM agent" if i < 2 else "diffusion model",
            subreddit="LocalLLaMA",
            created_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            url="https://x",
            selftext="",
        )
        for i in range(4)
    ]
    counts = reddit.mentions_per_term(posts, terms=["llm", "diffusion"])
    assert counts["llm"] == 2
    assert counts["diffusion"] == 2


# ---------- HTTP-layer integration tests (respx) ----------


_RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>r/LocalLLaMA: top posts of the week</title>
  <entry>
    <id>t3_aaa</id>
    <title>New 7B model drops</title>
    <link href="https://www.reddit.com/r/LocalLLaMA/comments/aaa/" />
    <updated>2026-05-13T12:00:00Z</updated>
    <published>2026-05-13T12:00:00Z</published>
    <summary type="html">&lt;p&gt;Performance is surprisingly strong.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <id>t3_bbb</id>
    <title>Best fine-tune recipe?</title>
    <link href="https://www.reddit.com/r/LocalLLaMA/comments/bbb/" />
    <updated>2026-05-12T08:00:00Z</updated>
    <published>2026-05-12T08:00:00Z</published>
    <summary type="html">&lt;p&gt;Anyone got tips&lt;/p&gt;</summary>
  </entry>
</feed>
"""


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipeline.fetch import _retry

    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(reddit.time, "sleep", lambda _s: None)


@respx.mock
def test_fetch_top_posts_parses_rss_per_subreddit(_no_sleep: None) -> None:
    respx.get(url__regex=r"https://www\.reddit\.com/r/LocalLLaMA/top/\.rss.*").mock(
        return_value=httpx.Response(200, text=_RSS_FIXTURE),
    )
    posts = reddit.fetch_top_posts(subreddits=["LocalLLaMA"], limit_per_sub=10)
    assert len(posts) == 2
    ids = {p.id for p in posts}
    assert ids == {"aaa", "bbb"}
    assert all(p.subreddit == "LocalLLaMA" for p in posts)


@respx.mock
def test_fetch_top_posts_skips_subreddit_when_rss_429(_no_sleep: None) -> None:
    respx.get(url__regex=r"https://www\.reddit\.com/r/Rate429/top/\.rss.*").mock(
        return_value=httpx.Response(429, headers={"retry-after": "1"}),
    )
    respx.get(url__regex=r"https://www\.reddit\.com/r/Healthy/top/\.rss.*").mock(
        return_value=httpx.Response(200, text=_RSS_FIXTURE),
    )
    posts = reddit.fetch_top_posts(subreddits=["Rate429", "Healthy"], limit_per_sub=10)
    # Rate429 retries inside _fetch_rss exhaust → exception caught at fetch_top_posts → skipped.
    # Healthy succeeds with 2 entries.
    assert len(posts) == 2
    assert all(p.subreddit == "Healthy" for p in posts)


@respx.mock
def test_fetch_top_posts_empty_subreddits_returns_empty() -> None:
    assert reddit.fetch_top_posts(subreddits=[]) == []


# ---------- OAuth path ----------


def test_oauth_token_returns_none_when_creds_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USERNAME",
        "REDDIT_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    assert reddit._oauth_token() is None


def test_oauth_token_returns_none_when_one_cred_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_USERNAME", "user")
    monkeypatch.setenv("REDDIT_PASSWORD", "   ")  # whitespace-only
    assert reddit._oauth_token() is None


@respx.mock
def test_oauth_token_round_trips_access_token(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_USERNAME", "user")
    monkeypatch.setenv("REDDIT_PASSWORD", "pass")
    route = respx.post(reddit.REDDIT_OAUTH_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "tok_42",
                "token_type": "bearer",
                "expires_in": 86400,
                "scope": "*",
            },
        )
    )
    assert reddit._oauth_token() == "tok_42"
    assert route.called


@respx.mock
def test_oauth_token_returns_none_on_401(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_USERNAME", "user")
    monkeypatch.setenv("REDDIT_PASSWORD", "wrong")
    respx.post(reddit.REDDIT_OAUTH_TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_grant"})
    )
    assert reddit._oauth_token() is None


@respx.mock
def test_fetch_top_oauth_parses_rich_json_fields(_no_sleep: None) -> None:
    """OAuth response carries score/upvote_ratio/num_comments — fields the
    RSS path can't fill. Confirm they round-trip into RedditPost."""
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "abc123",
                        "title": "Claude 4.7 just dropped",
                        "score": 412,
                        "upvote_ratio": 0.95,
                        "num_comments": 88,
                        "created_utc": 1747396800,  # 2026-05-16
                        "permalink": "/r/ClaudeAI/comments/abc123/claude_47/",
                        "selftext": "What a release.",
                    }
                },
                {
                    "data": {
                        # An item missing 'id' must be skipped, not raise
                        "title": "broken row",
                    }
                },
            ]
        }
    }
    respx.get(
        url__regex=r"https://oauth\.reddit\.com/r/ClaudeAI/top.*"
    ).mock(return_value=httpx.Response(200, json=payload))
    posts = reddit._fetch_top_oauth(
        "tok", "ClaudeAI", time_filter="week", limit=25
    )
    assert len(posts) == 1
    p = posts[0]
    assert p.id == "abc123"
    assert p.score == 412
    assert p.upvote_ratio == 0.95
    assert p.num_comments == 88
    assert p.subreddit == "ClaudeAI"


@respx.mock
def test_fetch_top_posts_prefers_oauth_when_token_available(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_USERNAME", "user")
    monkeypatch.setenv("REDDIT_PASSWORD", "pass")
    respx.post(reddit.REDDIT_OAUTH_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok"})
    )
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "x1",
                        "title": "OAuth-fetched",
                        "score": 1,
                        "upvote_ratio": 0.5,
                        "num_comments": 0,
                        "created_utc": 1747396800,
                        "permalink": "/r/LocalLLaMA/comments/x1/",
                        "selftext": "",
                    }
                }
            ]
        }
    }
    respx.get(url__regex=r"https://oauth\.reddit\.com/r/LocalLLaMA/top.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    # Make sure the RSS path is never invoked when OAuth succeeds
    rss_route = respx.get(
        url__regex=r"https://www\.reddit\.com/r/LocalLLaMA/top/\.rss.*"
    ).mock(return_value=httpx.Response(500))
    posts = reddit.fetch_top_posts(subreddits=["LocalLLaMA"], limit_per_sub=5)
    assert len(posts) == 1
    assert posts[0].title == "OAuth-fetched"
    assert not rss_route.called
