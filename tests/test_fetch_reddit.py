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


def test_oauth_token_returns_none_when_client_secret_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank CLIENT_SECRET short-circuits before any HTTP call.

    USERNAME/PASSWORD are no longer required (client_credentials is the
    default grant), so blank user creds alone won't return None — only
    missing CLIENT_ID or CLIENT_SECRET will.
    """
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "   ")  # whitespace-only
    monkeypatch.delenv("REDDIT_USERNAME", raising=False)
    monkeypatch.delenv("REDDIT_PASSWORD", raising=False)
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
def test_oauth_token_uses_client_credentials_grant_when_only_app_creds_set(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """USERNAME/PASSWORD are optional — CLIENT_ID + CLIENT_SECRET alone
    must yield a token via the client_credentials grant."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.delenv("REDDIT_USERNAME", raising=False)
    monkeypatch.delenv("REDDIT_PASSWORD", raising=False)

    captured: list[bytes] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content)
        return httpx.Response(200, json={"access_token": "tok_app_only"})

    respx.post(reddit.REDDIT_OAUTH_TOKEN_URL).mock(side_effect=_handler)
    assert reddit._oauth_token() == "tok_app_only"
    assert len(captured) == 1
    assert b"grant_type=client_credentials" in captured[0]
    # No username/password leak into the request body.
    assert b"username" not in captured[0]
    assert b"password" not in captured[0]


@respx.mock
def test_oauth_token_falls_back_to_password_grant_when_app_only_fails(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """If client_credentials is refused but USERNAME/PASSWORD are set,
    the password grant is attempted as a fallback."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("REDDIT_USERNAME", "user")
    monkeypatch.setenv("REDDIT_PASSWORD", "pass")

    grants: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "grant_type=client_credentials" in body:
            grants.append("client_credentials")
            return httpx.Response(401, json={"error": "unauthorized_client"})
        grants.append("password")
        return httpx.Response(200, json={"access_token": "tok_password"})

    respx.post(reddit.REDDIT_OAUTH_TOKEN_URL).mock(side_effect=_handler)
    assert reddit._oauth_token() == "tok_password"
    assert grants == ["client_credentials", "password"]


@respx.mock
def test_oauth_token_no_fallback_when_user_creds_missing(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """When client_credentials fails and no USERNAME/PASSWORD are set,
    return None without a second request — don't pointlessly retry."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "abc")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.delenv("REDDIT_USERNAME", raising=False)
    monkeypatch.delenv("REDDIT_PASSWORD", raising=False)

    route = respx.post(reddit.REDDIT_OAUTH_TOKEN_URL).mock(
        return_value=httpx.Response(401, json={"error": "unauthorized_client"})
    )
    assert reddit._oauth_token() is None
    assert route.call_count == 1


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
    arctic_route = respx.get(
        url__regex=r"https://arctic-shift\.photon-reddit\.com/.*"
    ).mock(return_value=httpx.Response(500))
    posts = reddit.fetch_top_posts(subreddits=["LocalLLaMA"], limit_per_sub=5)
    assert len(posts) == 1
    assert posts[0].title == "OAuth-fetched"
    assert not rss_route.called
    assert not arctic_route.called


# ---------- Arctic Shift archive fallback ----------


_ARCTIC_PAYLOAD = {
    "data": [
        {
            "id": "arc1",
            "title": "Mid-score post",
            "score": 50,
            "upvote_ratio": 0.9,
            "num_comments": 5,
            "created_utc": 1747396800,
            "permalink": "/r/LocalLLaMA/comments/arc1/",
            "selftext": "body",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/arc1/",
        },
        {
            "id": "arc2",
            "title": "Top-score post",
            "score": 999,
            "upvote_ratio": 0.97,
            "num_comments": 80,
            "created_utc": 1747396900,
            "permalink": "/r/LocalLLaMA/comments/arc2/",
            "selftext": "",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/arc2/",
        },
        {
            # Missing id → must be skipped without raising
            "title": "broken row",
            "score": 100,
        },
    ]
}


@respx.mock
def test_fetch_top_arctic_shift_parses_and_sorts_by_score(_no_sleep: None) -> None:
    respx.get(url__startswith=reddit.ARCTIC_SHIFT_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_ARCTIC_PAYLOAD),
    )
    posts = reddit._fetch_top_arctic_shift(
        "LocalLLaMA", time_filter="week", limit=2
    )
    assert len(posts) == 2
    # Sorted by score desc — arc2 (999) before arc1 (50).
    assert [p.id for p in posts] == ["arc2", "arc1"]
    assert posts[0].score == 999
    assert posts[0].upvote_ratio == 0.97
    assert posts[0].num_comments == 80
    assert posts[0].subreddit == "LocalLLaMA"
    assert posts[0].url.startswith("https://www.reddit.com/r/LocalLLaMA/")


@respx.mock
def test_fetch_top_arctic_shift_sends_subreddit_and_time_window(
    _no_sleep: None,
) -> None:
    captured: list[httpx.URL] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"data": []})

    respx.get(url__startswith=reddit.ARCTIC_SHIFT_SEARCH_URL).mock(
        side_effect=_handler
    )
    reddit._fetch_top_arctic_shift("ClaudeAI", time_filter="week", limit=10)
    assert len(captured) == 1
    qs = dict(captured[0].params)
    assert qs["subreddit"] == "ClaudeAI"
    assert qs["sort"] == "desc"
    # Oversample: limit*4 = 40, capped at ARCTIC_SHIFT_MAX_LIMIT=100.
    assert int(qs["limit"]) == 40
    # Time window for 'week' adds an `after` cutoff ~7 days back.
    assert "after" in qs
    cutoff = int(qs["after"])
    now = int(datetime.now(tz=timezone.utc).timestamp())
    seconds_in_7d = 7 * 24 * 3600
    # Allow a couple-minute window of clock skew between the test and the
    # cutoff Python computed inside the call.
    assert now - seconds_in_7d - 120 <= cutoff <= now - seconds_in_7d + 120


@respx.mock
def test_fetch_top_arctic_shift_omits_after_for_all_time_filter(
    _no_sleep: None,
) -> None:
    captured: list[httpx.URL] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"data": []})

    respx.get(url__startswith=reddit.ARCTIC_SHIFT_SEARCH_URL).mock(
        side_effect=_handler
    )
    reddit._fetch_top_arctic_shift("LocalLLaMA", time_filter="all", limit=5)
    qs = dict(captured[0].params)
    assert "after" not in qs


@respx.mock
def test_fetch_top_posts_falls_back_to_arctic_shift_when_rss_empty(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """No OAuth creds + RSS returns 0 entries → Arctic Shift fills the gap."""
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    # RSS returns an empty feed (200 OK, no entries) for every sub.
    respx.get(
        url__regex=r"https://www\.reddit\.com/r/LocalLLaMA/top/\.rss.*"
    ).mock(
        return_value=httpx.Response(
            200,
            text="<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'/>",
        )
    )
    respx.get(url__startswith=reddit.ARCTIC_SHIFT_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_ARCTIC_PAYLOAD)
    )
    posts = reddit.fetch_top_posts(subreddits=["LocalLLaMA"], limit_per_sub=10)
    assert len(posts) == 2
    assert {p.id for p in posts} == {"arc1", "arc2"}


@respx.mock
def test_fetch_top_posts_skips_arctic_shift_when_rss_succeeds(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """RSS returning posts must short-circuit before Arctic Shift fires."""
    for var in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    respx.get(
        url__regex=r"https://www\.reddit\.com/r/LocalLLaMA/top/\.rss.*"
    ).mock(return_value=httpx.Response(200, text=_RSS_FIXTURE))
    arctic_route = respx.get(
        url__startswith=reddit.ARCTIC_SHIFT_SEARCH_URL
    ).mock(return_value=httpx.Response(500))
    posts = reddit.fetch_top_posts(subreddits=["LocalLLaMA"], limit_per_sub=10)
    assert len(posts) == 2
    assert not arctic_route.called
