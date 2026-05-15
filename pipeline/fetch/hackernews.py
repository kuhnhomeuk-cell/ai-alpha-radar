"""Fetch AI-relevant Hacker News stories via the Algolia search API.

Per BACKEND_BUILD §7 Step 3 — bridge between research and builder mainstream.
Top comments hydrate for posts with > 5 replies; demand mining (Step 11)
consumes them.

v0.2.0 four-pass strategy: keyword sweep + Show HN + front_page + Ask HN.
Each pass uses different points thresholds and tag filters. Front_page
pass uses a post-fetch AI-relevance filter to strip hardware/general-news
noise that reaches the front page without our keyword vocabulary.

Note on Algolia query semantics: Algolia ANDs query words, so a single
"AI OR LLM" query returns near-zero hits. Each keyword is fired as its
own request and merged by objectID.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx
from pydantic import BaseModel

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL_TEMPLATE = "https://hn.algolia.com/api/v1/items/{item_id}"

# v0.2.0: tighter keyword set. "AI" and "model" dropped — too noisy
# without a points floor (catches hardware reviews, fashion models, etc.).
HN_KEYWORDS = [
    "LLM",
    "GPT",
    "Claude",
    "Gemini",
    "transformer",
    "fine-tuning",
    "embedding",
    "RAG",
    "MCP",
    "agents",
    "anthropic",
    "openai",
]

# Per-pass points floor. Keyword sweep is permissive; tag-only passes
# need a higher floor because they don't filter by AI vocabulary.
HN_MIN_POINTS_KEYWORD = 25
HN_MIN_POINTS_SHOW_HN = 50
HN_MIN_POINTS_FRONT_PAGE = 100
HN_MIN_POINTS_ASK_HN = 10

# Post-fetch relevance filter for the front_page pass. Catches AI stories
# that use phrasing outside HN_KEYWORDS (e.g. "Anthropic raises $2B").
AI_SIGNAL_TERMS: frozenset[str] = frozenset({
    "llm", "language model", "gpt", "claude", "gemini", "mistral", "llama",
    "anthropic", "openai", "deepmind", "transformer", "fine-tun", "embedding",
    "neural", "inference", "rag", "agent", "mcp", "model context",
    "diffusion", "stable diffusion", "image generation", "multimodal",
    "reinforcement learning", "rlhf", "copilot", "huggingface", "hugging face",
})

HN_HITS_PER_PAGE = 100
HN_REQUEST_INTERVAL_SECONDS = 0.3  # polite spacing; Algolia is generous
HN_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"


class HNComment(BaseModel):
    id: int
    author: str
    text: str
    created_at: datetime
    points: Optional[int] = None


class HNPost(BaseModel):
    id: int
    title: str
    url: Optional[str] = None
    points: int
    num_comments: int
    created_at: datetime
    story_text: Optional[str] = None
    author: str
    comments: Optional[list[HNComment]] = None


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_search_response(payload: dict[str, Any]) -> list[HNPost]:
    """Parse an Algolia /search response into HNPost objects."""
    posts: list[HNPost] = []
    for hit in payload.get("hits", []):
        try:
            object_id = int(hit["objectID"])
        except (KeyError, TypeError, ValueError):
            continue
        posts.append(
            HNPost(
                id=object_id,
                title=(hit.get("title") or "").strip(),
                url=hit.get("url"),
                points=int(hit.get("points") or 0),
                num_comments=int(hit.get("num_comments") or 0),
                created_at=_parse_iso(hit["created_at"]),
                story_text=hit.get("story_text"),
                author=(hit.get("author") or "").strip(),
            )
        )
    return posts


def parse_item_tree(tree: dict[str, Any], *, limit: int = 20) -> list[HNComment]:
    """Pull the first `limit` top-level comments from an Algolia /items/{id} tree."""
    children = tree.get("children") or []
    comments: list[HNComment] = []
    for child in children[:limit]:
        try:
            cid = int(child["id"])
        except (KeyError, TypeError, ValueError):
            continue
        text = child.get("text") or ""
        author = (child.get("author") or "").strip()
        if not author:
            continue  # deleted / dead comments
        comments.append(
            HNComment(
                id=cid,
                author=author,
                text=text,
                created_at=_parse_iso(child["created_at"]),
                points=child.get("points"),
            )
        )
    return comments


def attach_comments(post: HNPost, comments: list[HNComment]) -> HNPost:
    return post.model_copy(update={"comments": comments})


def _is_ai_relevant(post: HNPost) -> bool:
    """Cheap substring match for the front_page pass — strips RTX/M4/iPhone
    noise that hits the front page without our keyword vocabulary.
    """
    haystack = (post.title + " " + (post.story_text or "")).lower()
    return any(term in haystack for term in AI_SIGNAL_TERMS)


def _search(
    client: httpx.Client,
    keyword: str,
    cutoff_ts: int,
    *,
    min_points: int = HN_MIN_POINTS_KEYWORD,
) -> list[HNPost]:
    params = {
        "tags": "story",
        "query": keyword,
        "numericFilters": f"created_at_i>{cutoff_ts},points>{min_points}",
        "hitsPerPage": HN_HITS_PER_PAGE,
    }
    response = client.get(HN_SEARCH_URL, params=params)
    response.raise_for_status()
    return parse_search_response(response.json())


def _search_by_tag(
    client: httpx.Client,
    tags: str,
    cutoff_ts: int,
    *,
    min_points: int,
) -> list[HNPost]:
    """Tag-only pass — no keyword query, just tag + recency + points floor."""
    params = {
        "tags": tags,
        "numericFilters": f"created_at_i>{cutoff_ts},points>{min_points}",
        "hitsPerPage": HN_HITS_PER_PAGE,
    }
    response = client.get(HN_SEARCH_URL, params=params)
    response.raise_for_status()
    return parse_search_response(response.json())


def _safe_search(
    client: httpx.Client, keyword: str, cutoff_ts: int, *, min_points: int
) -> list[HNPost]:
    try:
        return _search(client, keyword, cutoff_ts, min_points=min_points)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"hn keyword pass failed for {keyword!r}: {e}", file=sys.stderr)
        return []


def _safe_search_by_tag(
    client: httpx.Client, tags: str, cutoff_ts: int, *, min_points: int
) -> list[HNPost]:
    try:
        return _search_by_tag(client, tags, cutoff_ts, min_points=min_points)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        print(f"hn tag pass failed for {tags!r}: {e}", file=sys.stderr)
        return []


def _fetch_item(client: httpx.Client, item_id: int) -> dict[str, Any]:
    response = client.get(HN_ITEM_URL_TEMPLATE.format(item_id=item_id))
    response.raise_for_status()
    return response.json()


def fetch_ai_posts(
    lookback_days: int = 7,
    *,
    keywords: Iterable[str] = HN_KEYWORDS,
    hydrate_top_n: int = 10,
) -> list[HNPost]:
    """Four-pass HN Algolia sweep, deduped by objectID.

    Pass 1: keyword sweep — one request per AI keyword, points > 25.
    Pass 2: Show HN — builder signal, points > 50.
    Pass 3: front_page — major AI stories that miss our keyword vocab;
            post-fetch AI relevance filter applied.
    Pass 4: Ask HN — demand signal, lower points floor (10).

    Top N most-discussed posts then get comment hydration for demand mining.
    """
    cutoff_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).timestamp())
    headers = {"User-Agent": HN_USER_AGENT}
    posts_by_id: dict[int, HNPost] = {}
    with httpx.Client(timeout=30, headers=headers) as client:
        # Pass 1: keyword sweep
        for kw in keywords:
            for post in _safe_search(client, kw, cutoff_ts, min_points=HN_MIN_POINTS_KEYWORD):
                posts_by_id.setdefault(post.id, post)
            time.sleep(HN_REQUEST_INTERVAL_SECONDS)

        # Pass 2: Show HN — builder signal
        for post in _safe_search_by_tag(
            client, "story,show_hn", cutoff_ts, min_points=HN_MIN_POINTS_SHOW_HN
        ):
            posts_by_id.setdefault(post.id, post)
        time.sleep(HN_REQUEST_INTERVAL_SECONDS)

        # Pass 3: front_page — high-points stories with AI relevance filter
        for post in _safe_search_by_tag(
            client, "story,front_page", cutoff_ts, min_points=HN_MIN_POINTS_FRONT_PAGE
        ):
            if _is_ai_relevant(post):
                posts_by_id.setdefault(post.id, post)
        time.sleep(HN_REQUEST_INTERVAL_SECONDS)

        # Pass 4: Ask HN — demand signal, lower floor
        for post in _safe_search_by_tag(
            client, "story,ask_hn", cutoff_ts, min_points=HN_MIN_POINTS_ASK_HN
        ):
            if _is_ai_relevant(post):
                posts_by_id.setdefault(post.id, post)
        time.sleep(HN_REQUEST_INTERVAL_SECONDS)

        # Hydrate top N most-discussed for demand mining
        candidates = sorted(
            (p for p in posts_by_id.values() if p.num_comments > 5),
            key=lambda p: p.num_comments,
            reverse=True,
        )
        for post in candidates[:hydrate_top_n]:
            try:
                tree = _fetch_item(client, post.id)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                print(f"hn item hydration failed for {post.id}: {e}", file=sys.stderr)
                continue
            posts_by_id[post.id] = attach_comments(post, parse_item_tree(tree, limit=20))
            time.sleep(HN_REQUEST_INTERVAL_SECONDS)

    return list(posts_by_id.values())


if __name__ == "__main__":
    import sys

    posts = fetch_ai_posts(lookback_days=7)
    with_pts = [p for p in posts if p.points > 0]
    hydrated = [p for p in posts if p.comments]
    print(
        f"fetched {len(posts)} unique AI-relevant posts in last 7d "
        f"({len(with_pts)} with points > 0, {len(hydrated)} hydrated)"
    )
    for p in sorted(posts, key=lambda p: -p.points)[:5]:
        print(f"  - [{p.points}pt {p.num_comments}c] {p.title}")
    if len(with_pts) < 30:
        sys.exit(1)
