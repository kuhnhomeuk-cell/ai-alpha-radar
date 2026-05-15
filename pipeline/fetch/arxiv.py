"""Fetch recent papers from the arXiv API.

Per BACKEND_BUILD §7 Step 2 — earliest signal (6-18 month lead time).
arXiv now enforces HTTPS (the spec's http:// URL 301-redirects), so we hit
https://export.arxiv.org directly.

v0.2.0 hardening:
- Server-side date filter via submittedDate in search_query (no more
  fetching 200 then dropping 80% client-side).
- Exponential backoff on 429 with jitter; arXiv tightened enforcement
  in Feb 2026.
- Pagination with a per-run request budget cap. On ArXivRateLimited,
  return partial results rather than losing the entire run.
- arxiv:comment extraction (venue boosts like "ICML2026" feed score.py).
- all_categories extraction (cross-listed categories for downstream
  filtering and dedup).
- Withdrawn papers filtered out.
"""

from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import feedparser
import httpx
from pydantic import BaseModel

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_REQUEST_INTERVAL_SECONDS = 3.0
ARXIV_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"

PAGE_SIZE = 100
MAX_REQUESTS_PER_RUN = 10
BACKOFF_BASE_DELAY = 3.0
BACKOFF_MAX_RETRIES = 4

WITHDRAWN_MARKER = "This submission has been withdrawn"


class ArXivRateLimited(Exception):
    """Raised when arXiv 429s persist through all retries.

    Caller is expected to catch this and return whatever partial data has
    already been collected, rather than discarding the whole run.
    """


class Paper(BaseModel):
    id: str
    title: str
    abstract: str
    authors: list[str]
    published_at: datetime
    primary_category: str
    url: str
    # v0.2.0 additions — optional with defaults for backwards compat with
    # snapshots that pre-date these fields.
    all_categories: list[str] = []
    comment: str = ""


def _parse_arxiv_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_atom_feed(xml_text: str, *, categories: Iterable[str]) -> list[Paper]:
    """Parse an arXiv Atom XML response, keeping only entries whose primary
    category is in the supplied set. Withdrawn papers are dropped.
    """
    allowed = set(categories)
    feed = feedparser.parse(xml_text)
    papers: list[Paper] = []
    for entry in feed.entries:
        primary = entry.get("arxiv_primary_category", {}).get("term", "")
        if primary not in allowed:
            continue
        summary = entry.get("summary", "")
        if WITHDRAWN_MARKER in summary:
            continue
        authors = [a.get("name", "").strip() for a in entry.get("authors", [])]
        authors = [a for a in authors if a]
        published_iso = entry.get("published") or entry.get("updated") or ""
        all_cats = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
        comment = entry.get("arxiv_comment", "") or ""
        papers.append(
            Paper(
                id=entry.id,
                title=entry.title.strip(),
                abstract=summary.strip(),
                authors=authors,
                published_at=_parse_arxiv_datetime(published_iso),
                primary_category=primary,
                url=entry.link,
                all_categories=all_cats,
                comment=comment.strip(),
            )
        )
    return papers


def _date_range_query(categories: list[str], lookback_days: int) -> str:
    """Build a search_query string with a server-side submittedDate filter.

    Format: `(cat:cs.AI OR cat:cs.LG) AND submittedDate:[YYYYMMDDhhmm TO YYYYMMDDhhmm]`
    """
    now = datetime.now(tz=timezone.utc)
    until = now + timedelta(hours=1)  # buffer for announce-time skew
    since = now - timedelta(days=lookback_days)
    fmt = "%Y%m%d%H%M"
    cat_filter = " OR ".join(f"cat:{c}" for c in categories)
    return f"({cat_filter}) AND submittedDate:[{since.strftime(fmt)} TO {until.strftime(fmt)}]"


def _fetch_with_backoff(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int = BACKOFF_MAX_RETRIES,
    base_delay: float = BACKOFF_BASE_DELAY,
) -> str:
    """GET with exponential backoff. Retries on 429 and 5xx.

    Delays: 3s, 6s, 12s, 24s (+ up to 1s jitter per attempt).
    Raises ArXivRateLimited after the final attempt also fails with 429.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = client.get(url, params=params)
            if response.status_code == 429:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(delay)
                    continue
                raise ArXivRateLimited(f"arXiv 429 after {max_retries} attempts")
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as e:
            last_exc = e
            if e.response.status_code == 429 and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
            if e.response.status_code >= 500 and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable")


def fetch_recent_papers(
    categories: list[str],
    lookback_days: int = 2,
    *,
    max_requests: int = MAX_REQUESTS_PER_RUN,
) -> list[Paper]:
    """Live arXiv query with server-side date filter, pagination, and backoff.

    Default lookback is 2 days because arXiv batches its API release once daily
    at ~17:00 UTC; a strict 24-hour window misses the most recent batch.

    On ArXivRateLimited, returns whatever partial results were collected.
    """
    search_query = _date_range_query(categories, lookback_days)
    headers = {"User-Agent": ARXIV_USER_AGENT}
    all_papers: list[Paper] = []
    seen_ids: set[str] = set()
    requests_made = 0

    with httpx.Client(timeout=30, headers=headers) as client:
        start = 0
        while requests_made < max_requests:
            params = {
                "search_query": search_query,
                "start": start,
                "max_results": PAGE_SIZE,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            try:
                xml_text = _fetch_with_backoff(client, ARXIV_API_URL, params)
            except ArXivRateLimited:
                print(
                    f"arxiv: rate-limited after {requests_made} requests, "
                    f"returning {len(all_papers)} partial results",
                    file=sys.stderr,
                )
                break
            requests_made += 1

            page_papers = parse_atom_feed(xml_text, categories=categories)
            new_count = 0
            for p in page_papers:
                if p.id in seen_ids:
                    continue
                seen_ids.add(p.id)
                all_papers.append(p)
                new_count += 1

            # Stop when this page returned nothing new (exhausted result set).
            if not page_papers or new_count == 0:
                break

            start += PAGE_SIZE
            time.sleep(ARXIV_REQUEST_INTERVAL_SECONDS)

    return all_papers


if __name__ == "__main__":
    target = ["cs.AI", "cs.LG", "cs.CL"]
    lookback = 2
    papers = fetch_recent_papers(target, lookback_days=lookback)
    print(f"fetched {len(papers)} papers in last {lookback}d (primary in {target})")
    for p in papers[:3]:
        print(f"  - [{p.primary_category}] {p.title}")
    if len(papers) < 10:
        sys.exit(1)
