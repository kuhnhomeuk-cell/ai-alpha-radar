"""Fetch trending AI posts from public Reddit JSON endpoints.

Per Path C buildout — practitioner-sentiment source that AlphaSignal and
TLDR both blind-spot. The /r/{sub}/top.json?t=day endpoint is public,
unauthenticated, and free for non-commercial use; the only requirement
is a unique User-Agent string.

Per-sub score thresholds tuned to the cohort: r/MachineLearning is
research-heavy (lower bar), r/LocalLLaMA is high-volume (higher bar),
the general subs (singularity, OpenAI, ClaudeAI) get an AI-relevance
filter on top of the score floor.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx
from pydantic import BaseModel

REDDIT_API_BASE = "https://www.reddit.com"
REDDIT_REQUEST_TIMEOUT = 20.0
REDDIT_REQUEST_INTERVAL_SECONDS = 1.0  # well inside the 60 req/min free limit
REDDIT_USER_AGENT = "ai-alpha-radar/0.2 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"

# Per-subreddit score floor — calibrated so each sub contributes roughly
# 20-40 high-signal posts per daily run.
REDDIT_SUBS: dict[str, int] = {
    "MachineLearning": 50,   # research-heavy, lower bar
    "LocalLLaMA": 100,       # high-volume, higher bar
    "singularity": 200,      # general sub, AI-keyword filter applied
    "OpenAI": 100,
    "ClaudeAI": 50,
    "artificial": 100,
}

# Subs that need an AI-relevance keyword check (broader-topic subs).
REDDIT_SUBS_REQUIRING_AI_FILTER: frozenset[str] = frozenset({"singularity", "artificial"})

# AI keyword vocabulary for the post-fetch relevance filter.
REDDIT_AI_KEYWORDS: frozenset[str] = frozenset({
    "ai", "llm", "gpt", "claude", "gemini", "anthropic", "openai", "deepmind",
    "model", "transformer", "neural", "agent", "rag", "mcp", "fine-tun",
    "embedding", "diffusion", "huggingface", "hugging face", "llama", "mistral",
    "reasoning", "inference", "training",
})


class RedditPost(BaseModel):
    id: str  # reddit's t3_xxxxx prefix is stripped; this is the bare id
    subreddit: str
    title: str
    url: str  # external link or self-post permalink
    permalink: str  # always https://reddit.com/r/{sub}/comments/...
    author: str
    selftext: str = ""
    score: int
    num_comments: int
    created_at: datetime
    is_self: bool = False


def _parse_post(child: dict[str, Any]) -> Optional[RedditPost]:
    """Parse a single Reddit listing child into a RedditPost. None on bad data."""
    data = child.get("data") or {}
    post_id = data.get("id")
    if not post_id:
        return None
    try:
        return RedditPost(
            id=post_id,
            subreddit=data.get("subreddit", ""),
            title=(data.get("title") or "").strip(),
            url=data.get("url") or f"https://reddit.com{data.get('permalink', '')}",
            permalink=f"https://reddit.com{data.get('permalink', '')}",
            author=data.get("author", ""),
            selftext=(data.get("selftext") or "")[:2000],
            score=int(data.get("score") or 0),
            num_comments=int(data.get("num_comments") or 0),
            created_at=datetime.fromtimestamp(
                float(data.get("created_utc") or 0), tz=timezone.utc
            ),
            is_self=bool(data.get("is_self")),
        )
    except (TypeError, ValueError):
        return None


def parse_listing_response(payload: dict[str, Any]) -> list[RedditPost]:
    """Parse a Reddit /r/{sub}/top.json listing into RedditPost objects."""
    data = payload.get("data") or {}
    children = data.get("children") or []
    posts: list[RedditPost] = []
    for child in children:
        post = _parse_post(child)
        if post is not None:
            posts.append(post)
    return posts


def _is_ai_relevant(post: RedditPost) -> bool:
    """Substring match on title + selftext for general subs."""
    haystack = (post.title + " " + post.selftext).lower()
    return any(term in haystack for term in REDDIT_AI_KEYWORDS)


def _safe_fetch_sub(
    client: httpx.Client, subreddit: str, *, limit: int, timeframe: str = "day"
) -> list[RedditPost]:
    """Fetch one sub's top posts. Returns [] on any error."""
    url = f"{REDDIT_API_BASE}/r/{subreddit}/top.json"
    params = {"t": timeframe, "limit": min(limit, 100)}
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        return parse_listing_response(response.json())
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"reddit fetch failed for r/{subreddit}: {e}", file=sys.stderr)
        return []


def fetch_ai_posts(
    *,
    subs: Iterable[str] = tuple(REDDIT_SUBS.keys()),
    timeframe: str = "day",
    limit_per_sub: int = 100,
) -> list[RedditPost]:
    """Fetch top posts from each AI subreddit, filter by per-sub score
    threshold + optional AI-relevance check, dedupe by post id.
    """
    headers = {"User-Agent": REDDIT_USER_AGENT}
    posts_by_id: dict[str, RedditPost] = {}
    with httpx.Client(timeout=REDDIT_REQUEST_TIMEOUT, headers=headers) as client:
        for sub in subs:
            threshold = REDDIT_SUBS.get(sub, 50)
            needs_filter = sub in REDDIT_SUBS_REQUIRING_AI_FILTER
            for post in _safe_fetch_sub(client, sub, limit=limit_per_sub, timeframe=timeframe):
                if post.score < threshold:
                    continue
                if needs_filter and not _is_ai_relevant(post):
                    continue
                posts_by_id.setdefault(post.id, post)
            time.sleep(REDDIT_REQUEST_INTERVAL_SECONDS)
    return list(posts_by_id.values())


if __name__ == "__main__":
    posts = fetch_ai_posts()
    print(f"fetched {len(posts)} unique top posts across {len(REDDIT_SUBS)} subs")
    for p in sorted(posts, key=lambda p: -p.score)[:10]:
        print(f"  - [r/{p.subreddit} {p.score}pt {p.num_comments}c] {p.title[:80]}")
    if len(posts) < 10:
        sys.exit(1)
