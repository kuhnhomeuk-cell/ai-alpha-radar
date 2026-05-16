"""Product Hunt GraphQL v2 trending launches.

Audit item 3.4. PH typically surfaces creator-tool launches 1-2 days
ahead of HN for solo-creator products. GraphQL endpoint requires a
Bearer token (free, from the developer dashboard).

Returns [] silently when PRODUCT_HUNT_TOKEN is missing so the daily
run doesn't crash on operator misconfig.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

PH_API_URL = "https://api.producthunt.com/v2/api/graphql"
PH_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"
PH_REQUEST_INTERVAL_SECONDS = 1.0

# Allow-list of topic names that map to "creator-relevant". Inclusive
# because the audit niche is "AI tools for solo creators" — any of
# these counts.
CREATOR_TOPICS = {
    "Artificial Intelligence",
    "Developer Tools",
    "Productivity",
    "Video",
    "Design Tools",
    "Marketing",
    "SaaS",
    "No-Code",
}

PH_QUERY = """
query Trending($postedAfter: DateTime!) {
  posts(featured: true, postedAfter: $postedAfter, order: VOTES) {
    edges {
      node {
        id
        name
        tagline
        url
        votesCount
        createdAt
        topics(first: 5) {
          edges { node { name } }
        }
      }
    }
  }
}
"""


class ProductHuntLaunch(BaseModel):
    id: str
    name: str
    tagline: str
    url: str
    votes_count: int
    created_at: datetime
    topics: list[str]


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_response(payload: dict[str, Any]) -> list[ProductHuntLaunch]:
    """Parse a Product Hunt GraphQL response body."""
    edges = payload.get("data", {}).get("posts", {}).get("edges", []) if payload else []
    out: list[ProductHuntLaunch] = []
    for e in edges:
        node = e.get("node") or {}
        if not node.get("id"):
            continue
        topics = [
            t.get("node", {}).get("name")
            for t in (node.get("topics", {}).get("edges") or [])
            if t.get("node", {}).get("name")
        ]
        out.append(
            ProductHuntLaunch(
                id=node["id"],
                name=node.get("name", ""),
                tagline=node.get("tagline", ""),
                url=node.get("url", ""),
                votes_count=int(node.get("votesCount") or 0),
                created_at=_parse_iso(node.get("createdAt", "1970-01-01T00:00:00Z")),
                topics=topics,
            )
        )
    return out


def filter_creator_relevant(
    launches: Sequence[ProductHuntLaunch], *, allow: Optional[set[str]] = None
) -> list[ProductHuntLaunch]:
    allow = allow or CREATOR_TOPICS
    return [
        launch for launch in launches if any(t in allow for t in launch.topics)
    ]


def launches_per_term(
    launches: Sequence[ProductHuntLaunch], *, terms: Sequence[str]
) -> dict[str, int]:
    out: dict[str, int] = {t: 0 for t in terms}
    docs = [(launch.name + " " + launch.tagline).lower() for launch in launches]
    for t in terms:
        needle = t.lower()
        out[t] = sum(1 for d in docs if needle in d)
    return out


@with_retry(attempts=3, base_delay=1.0)
def _post_graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": PH_USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30, headers=headers) as client:
        response = client.post(
            PH_API_URL, json={"query": query, "variables": variables}
        )
        response.raise_for_status()
        return response.json()


def fetch_trending_launches(
    *, lookback_days: int = 7
) -> list[ProductHuntLaunch]:  # pragma: no cover — live API
    """Live GraphQL fetch. Returns [] when PRODUCT_HUNT_TOKEN is unset.

    Failure surfacing: HTTP-level failures (auth drift, schema change) log to
    stderr but still return []. The bare `except: return []` swallowed every
    signal — operators had no way to tell if the token was bad, the query
    rejected, or the day was empty. Now the diagnostic stays in the log even
    though the pipeline continues to ship.
    """
    token = os.environ.get("PRODUCT_HUNT_TOKEN", "").strip()
    if not token:
        print(
            "producthunt: PRODUCT_HUNT_TOKEN missing — skipping launches",
            file=sys.stderr,
        )
        return []
    posted_after = (
        datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
    ).isoformat()
    try:
        payload = _post_graphql(PH_QUERY, {"postedAfter": posted_after}, token)
    except Exception as e:
        print(
            f"producthunt: {type(e).__name__} fetching trending launches: {e}",
            file=sys.stderr,
        )
        return []
    time.sleep(PH_REQUEST_INTERVAL_SECONDS)
    return filter_creator_relevant(parse_response(payload))


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(".env.local", override=True)
    token_set = bool(os.environ.get("PRODUCT_HUNT_TOKEN", "").strip())
    print(f"auth: {'PRODUCT_HUNT_TOKEN set' if token_set else 'UNAUTHENTICATED (skip)'}")
    launches = fetch_trending_launches(lookback_days=7)
    print(f"fetched {len(launches)} creator-relevant launches in last 7d")
    for launch in launches[:5]:
        print(f"  - [{launch.votes_count} votes] {launch.name}: {launch.tagline}")
    # Healthy: when authenticated and PH has any creator-tool launches,
    # we expect ≥1. Unauth path exits 0 so the verifier still works for
    # CI smoke tests without leaking secrets.
    if token_set and not launches:
        sys.exit(1)
