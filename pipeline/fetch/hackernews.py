"""Fetch AI-relevant Hacker News stories via the Algolia search API.

Per BACKEND_BUILD §7 Step 3 — bridge between research and builder mainstream.
Top comments hydrate for posts with > 5 replies; demand mining (Step 11)
consumes them.

Note on the spec's literal URL: the spec wrote `query=AI+OR+LLM+OR+GPT+OR+Claude+OR+model`
under the assumption Algolia parses `OR`. It does not — Algolia ANDs query
words and returns near-zero hits for the joined phrase. We instead fire one
single-keyword search per term and dedupe by `objectID`.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx
from pydantic import BaseModel

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL_TEMPLATE = "https://hn.algolia.com/api/v1/items/{item_id}"
HN_KEYWORDS = ["AI", "LLM", "GPT", "Claude", "model"]
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


def _search(client: httpx.Client, keyword: str, cutoff_ts: int) -> list[HNPost]:
    params = {
        "tags": "story",
        "query": keyword,
        "numericFilters": f"created_at_i>{cutoff_ts}",
        "hitsPerPage": HN_HITS_PER_PAGE,
    }
    response = client.get(HN_SEARCH_URL, params=params)
    response.raise_for_status()
    return parse_search_response(response.json())


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
    """Live HN Algolia search across each AI keyword, dedupe by id, then
    hydrate top-N comment threads for posts with > 5 comments.
    """
    cutoff_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).timestamp())
    headers = {"User-Agent": HN_USER_AGENT}
    posts_by_id: dict[int, HNPost] = {}
    with httpx.Client(timeout=30, headers=headers) as client:
        for kw in keywords:
            for post in _search(client, kw, cutoff_ts):
                posts_by_id.setdefault(post.id, post)
            time.sleep(HN_REQUEST_INTERVAL_SECONDS)

        # hydrate the top N most-discussed threads for demand mining
        candidates = sorted(
            (p for p in posts_by_id.values() if p.num_comments > 5),
            key=lambda p: p.num_comments,
            reverse=True,
        )
        for post in candidates[:hydrate_top_n]:
            tree = _fetch_item(client, post.id)
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
