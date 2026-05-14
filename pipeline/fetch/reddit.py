"""Reddit subreddit-shortlist fetcher.

Audit item 3.3 — Theme 7: r/LocalLLaMA, r/StableDiffusion, r/ClaudeAI
typically surface creator-tool launches 5-10 days ahead of HN. Reddit
script-app auth grants 60 RPM free; we fan out across the curated
subreddit list in data/reddit_subreddits.json and dedupe by post id.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from pydantic import BaseModel

DEFAULT_SUBREDDITS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "reddit_subreddits.json"
)
REDDIT_USER_AGENT_FALLBACK = "ai-alpha-radar/0.1 by /u/anonymous"


class RedditPost(BaseModel):
    id: str
    title: str
    subreddit: str
    score: int
    upvote_ratio: float
    num_comments: int
    created_at: datetime
    url: str
    selftext: str = ""


def parse_submission(submission: Any) -> RedditPost:
    """Convert a PRAW Submission (or stand-in with the same attrs) into RedditPost."""
    return RedditPost(
        id=getattr(submission, "id"),
        title=getattr(submission, "title", "").strip(),
        subreddit=getattr(submission.subreddit, "display_name", str(submission.subreddit)),
        score=int(getattr(submission, "score", 0) or 0),
        upvote_ratio=float(getattr(submission, "upvote_ratio", 0.5) or 0.5),
        num_comments=int(getattr(submission, "num_comments", 0) or 0),
        created_at=datetime.fromtimestamp(
            getattr(submission, "created_utc", 0), tz=timezone.utc
        ),
        url=getattr(submission, "url", ""),
        selftext=getattr(submission, "selftext", "") or "",
    )


def engagement_score(post: RedditPost, *, now: Optional[datetime] = None) -> float:
    """audit's recipe: (upvote_ratio * score * num_comments) / age_hours, with floor."""
    now = now or datetime.now(tz=timezone.utc)
    age_hours = max((now - post.created_at).total_seconds() / 3600.0, 1.0)
    return (post.upvote_ratio * post.score * max(post.num_comments, 1)) / age_hours


def dedupe_posts(posts: Iterable[RedditPost]) -> list[RedditPost]:
    seen: set[str] = set()
    out: list[RedditPost] = []
    for p in posts:
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def _post_text_lower(p: RedditPost) -> str:
    return (p.title + " " + p.selftext).lower()


def mentions_per_term(
    posts: Sequence[RedditPost], *, terms: Sequence[str]
) -> dict[str, int]:
    """Per-term: count of posts whose title+selftext contains the term (case-insensitive)."""
    out: dict[str, int] = {t: 0 for t in terms}
    texts = [(_post_text_lower(p)) for p in posts]
    for term in terms:
        needle = term.lower()
        out[term] = sum(1 for text in texts if needle in text)
    return out


def top_subreddit_per_term(
    posts: Sequence[RedditPost], *, terms: Sequence[str]
) -> dict[str, str]:
    """Per-term: subreddit with the most matching posts. Ties broken by name."""
    out: dict[str, str] = {}
    for term in terms:
        needle = term.lower()
        counts: dict[str, int] = {}
        for p in posts:
            if needle in _post_text_lower(p):
                counts[p.subreddit] = counts.get(p.subreddit, 0) + 1
        if counts:
            out[term] = max(sorted(counts), key=lambda k: counts[k])
    return out


def load_subreddit_list(path: Path = DEFAULT_SUBREDDITS_PATH) -> list[str]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _build_client(client_id: str, client_secret: str, user_agent: str):  # pragma: no cover
    import praw

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_updates=False,
    )


def fetch_top_posts(
    *,
    subreddits: Optional[Sequence[str]] = None,
    time_filter: str = "week",
    limit_per_sub: int = 25,
) -> list[RedditPost]:  # pragma: no cover — live API call
    """Fan out across subreddits, fetch top-of-week, parse + dedupe.

    Reads REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT from
    the environment. Returns [] if any required cred is missing.
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return []
    user_agent = os.environ.get("REDDIT_USER_AGENT", REDDIT_USER_AGENT_FALLBACK)
    if subreddits is None:
        subreddits = load_subreddit_list()
    if not subreddits:
        return []

    client = _build_client(client_id, client_secret, user_agent)
    out: list[RedditPost] = []
    for sub_name in subreddits:
        try:
            sub = client.subreddit(sub_name)
            for submission in sub.top(time_filter=time_filter, limit=limit_per_sub):
                out.append(parse_submission(submission))
        except Exception:
            continue
    return dedupe_posts(out)
