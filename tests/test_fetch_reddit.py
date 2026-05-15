"""TDD for pipeline.fetch.reddit — PRAW-shaped Reddit fetcher (audit 3.3)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from pipeline.fetch import reddit


def _fake_submission(
    *,
    id: str,
    title: str,
    subreddit: str,
    score: int,
    upvote_ratio: float,
    num_comments: int,
    created_utc: float,
    selftext: str = "",
):
    return SimpleNamespace(
        id=id,
        title=title,
        subreddit=SimpleNamespace(display_name=subreddit),
        score=score,
        upvote_ratio=upvote_ratio,
        num_comments=num_comments,
        created_utc=created_utc,
        selftext=selftext,
        url=f"https://reddit.com/r/{subreddit}/comments/{id}",
    )


def test_parse_submission_extracts_fields() -> None:
    sub = _fake_submission(
        id="abc",
        title="LLM tool launch",
        subreddit="LocalLLaMA",
        score=120,
        upvote_ratio=0.95,
        num_comments=44,
        created_utc=datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp(),
    )
    post = reddit.parse_submission(sub)
    assert post.title == "LLM tool launch"
    assert post.subreddit == "LocalLLaMA"
    assert post.score == 120
    assert post.num_comments == 44


def test_engagement_score_age_decay() -> None:
    now = datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)
    old = reddit.RedditPost(
        id="x",
        title="t",
        subreddit="s",
        score=100,
        upvote_ratio=0.9,
        num_comments=20,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        url="https://x",
        selftext="",
    )
    fresh = reddit.RedditPost(
        id="y",
        title="t",
        subreddit="s",
        score=100,
        upvote_ratio=0.9,
        num_comments=20,
        created_at=datetime(2026, 5, 13, 6, tzinfo=timezone.utc),
        url="https://x",
        selftext="",
    )
    assert reddit.engagement_score(fresh, now=now) > reddit.engagement_score(old, now=now)


def test_dedupe_by_id() -> None:
    base = dict(
        title="t",
        subreddit="s",
        score=1,
        upvote_ratio=0.5,
        num_comments=0,
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
            score=10,
            upvote_ratio=0.9,
            num_comments=5,
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
            score=10,
            upvote_ratio=0.9,
            num_comments=5,
            created_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            url="https://x",
            selftext="",
        )
        for i in range(4)
    ]
    counts = reddit.mentions_per_term(posts, terms=["llm", "diffusion"])
    assert counts["llm"] == 2
    assert counts["diffusion"] == 2
