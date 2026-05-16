"""Fetch recent papers from the arXiv API.

Per BACKEND_BUILD §7 Step 2 — earliest signal (6-18 month lead time).
arXiv now enforces HTTPS (the spec's http:// URL 301-redirects), so we hit
https://export.arxiv.org directly.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_REQUEST_INTERVAL_SECONDS = 3.0
ARXIV_MAX_RESULTS = 200
ARXIV_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"


class Paper(BaseModel):
    id: str
    title: str
    abstract: str
    authors: list[str]
    published_at: datetime
    primary_category: str
    url: str
    # v0.2.0 — cross-listed categories (e.g. cs.AI primary + cs.LG cross-list)
    # and arxiv:comment (often carries venue acceptance like "ICML2026").
    # Both optional with defaults so older snapshots round-trip.
    all_categories: list[str] = []
    comment: str = ""


WITHDRAWN_MARKER = "This submission has been withdrawn"


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
        comment = (entry.get("arxiv_comment", "") or "").strip()
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
                comment=comment,
            )
        )
    return papers


@with_retry(attempts=3, base_delay=1.0)
def fetch_recent_papers(categories: list[str], lookback_days: int = 2) -> list[Paper]:
    """Live arXiv query. One request, then a 3-second hard sleep per the rate cap.

    Default lookback is 2 days because arXiv batches its API release once daily at
    ~17:00 UTC; a strict 24-hour window misses the most recent batch depending on
    time-of-day. Two days absorbs one full announce cycle.
    """
    # arXiv expects ' OR ' (with spaces) as the boolean separator; spaces URL-encode
    # to '+' on the wire. Joining with literal '+' instead would send a single
    # unparseable token after URL encoding (httpx encodes '+' as '%2B').
    search_query = " OR ".join(f"cat:{c}" for c in categories)
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": ARXIV_MAX_RESULTS,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    headers = {"User-Agent": ARXIV_USER_AGENT}
    with httpx.Client(timeout=30, headers=headers) as client:
        response = client.get(ARXIV_API_URL, params=params)
        response.raise_for_status()
        xml_text = response.text
    time.sleep(ARXIV_REQUEST_INTERVAL_SECONDS)

    papers = parse_atom_feed(xml_text, categories=categories)
    if lookback_days:
        cutoff = datetime.now(tz=timezone.utc).timestamp() - lookback_days * 86400
        papers = [p for p in papers if p.published_at.timestamp() >= cutoff]
    return papers


if __name__ == "__main__":
    import sys

    target = ["cs.AI", "cs.LG", "cs.CL"]
    lookback = 2
    papers = fetch_recent_papers(target, lookback_days=lookback)
    print(f"fetched {len(papers)} papers in last {lookback}d (primary in {target})")
    for p in papers[:3]:
        print(f"  - [{p.primary_category}] {p.title}")
    if len(papers) < 10:
        sys.exit(1)
