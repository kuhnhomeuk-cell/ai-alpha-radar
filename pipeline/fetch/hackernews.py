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

from pipeline.fetch._retry import with_retry

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL_TEMPLATE = "https://hn.algolia.com/api/v1/items/{item_id}"

# v0.2.0 — tighter default keyword set. "AI" and "model" dropped because
# they catch hardware reviews and fashion-model articles without a points
# floor. Callers that need the legacy vocabulary can pass `keywords=...`.
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

# v0.2.0 — per-pass points floor. Keyword sweep is permissive; tag-only
# passes (Show HN / front_page / Ask HN) need higher floors because they
# don't filter by AI vocabulary. Defaults: passing min_points=0 (the
# legacy behavior) is still allowed.
HN_MIN_POINTS_KEYWORD = 25
HN_MIN_POINTS_SHOW_HN = 50
HN_MIN_POINTS_FRONT_PAGE = 100
HN_MIN_POINTS_ASK_HN = 10

# v0.2.0 — post-fetch AI relevance filter for tag-only passes (front_page
# and Ask HN). The keyword sweep is already AI-constrained; tag-only
# passes apply this filter to strip RTX/M4/iPhone noise that reaches the
# front page without our keyword vocabulary.
#
# Single source of truth: `pipeline.niche_filter.CREATOR_NICHE_TERMS`. Kept
# as a module-local alias so existing imports of `hackernews.AI_SIGNAL_TERMS`
# don't break.
from pipeline.niche_filter import CREATOR_NICHE_TERMS as AI_SIGNAL_TERMS

# Tag-only extra passes that run.py opts in to. Default behavior of
# fetch_ai_posts (keyword-only) is preserved for tests and ad-hoc callers.
EXTRA_PASS_NAMES = ("show_hn", "front_page", "ask_hn")

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
    """Cheap substring match for tag-only passes (front_page / Ask HN).
    Strips hardware-review / general-news noise that hits the front page
    without our keyword vocabulary.
    """
    haystack = (post.title + " " + (post.story_text or "")).lower()
    return any(term in haystack for term in AI_SIGNAL_TERMS)


def _search(
    client: httpx.Client,
    keyword: str,
    cutoff_ts: int,
    *,
    min_points: int = 0,
) -> list[HNPost]:
    """Keyword search. min_points=0 (default) preserves legacy behavior so
    existing tests don't need to thread the parameter; v0.2.0 callers can
    pass min_points=HN_MIN_POINTS_KEYWORD to tighten quality.
    """
    numeric_filters = f"created_at_i>{cutoff_ts}"
    if min_points > 0:
        numeric_filters += f",points>{min_points}"
    params = {
        "tags": "story",
        "query": keyword,
        "numericFilters": numeric_filters,
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


def _fetch_item(client: httpx.Client, item_id: int) -> dict[str, Any]:
    response = client.get(HN_ITEM_URL_TEMPLATE.format(item_id=item_id))
    response.raise_for_status()
    return response.json()


@with_retry(attempts=3, base_delay=1.0)
def fetch_ai_posts(
    lookback_days: int = 7,
    *,
    keywords: Iterable[str] = HN_KEYWORDS,
    hydrate_top_n: int = 10,
    min_points: int = 0,
    extra_passes: tuple[str, ...] = (),
) -> list[HNPost]:
    """Live HN Algolia search across each AI keyword, dedupe by id, then
    hydrate top-N comment threads for posts with > 5 comments.

    v0.2.0 additions (opt-in, default-off for backwards compatibility):
      - `min_points`: API-level points floor for the keyword sweep.
      - `extra_passes`: subset of {"show_hn", "front_page", "ask_hn"}.
        Each extra pass is a tag-only sweep with its own points floor;
        front_page and Ask HN pass through `_is_ai_relevant` to strip
        non-AI noise. Run.py enables all three for the daily snapshot.
    """
    cutoff_ts = int((datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).timestamp())
    headers = {"User-Agent": HN_USER_AGENT}
    posts_by_id: dict[int, HNPost] = {}
    with httpx.Client(timeout=30, headers=headers) as client:
        # Pass 1 — keyword sweep
        for kw in keywords:
            for post in _search(client, kw, cutoff_ts, min_points=min_points):
                posts_by_id.setdefault(post.id, post)
            time.sleep(HN_REQUEST_INTERVAL_SECONDS)

        # Optional extra passes — tag-only sweeps with per-pass thresholds.
        if "show_hn" in extra_passes:
            for post in _search_by_tag(
                client, "story,show_hn", cutoff_ts, min_points=HN_MIN_POINTS_SHOW_HN
            ):
                posts_by_id.setdefault(post.id, post)
            time.sleep(HN_REQUEST_INTERVAL_SECONDS)
        if "front_page" in extra_passes:
            for post in _search_by_tag(
                client, "story,front_page", cutoff_ts, min_points=HN_MIN_POINTS_FRONT_PAGE
            ):
                if _is_ai_relevant(post):
                    posts_by_id.setdefault(post.id, post)
            time.sleep(HN_REQUEST_INTERVAL_SECONDS)
        if "ask_hn" in extra_passes:
            for post in _search_by_tag(
                client, "story,ask_hn", cutoff_ts, min_points=HN_MIN_POINTS_ASK_HN
            ):
                if _is_ai_relevant(post):
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
