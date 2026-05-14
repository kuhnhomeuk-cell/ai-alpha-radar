"""Newsletter RSS cross-mention signal aggregator.

Audit item 3.2 — Theme 7: niche AI newsletters typically surface
creator-relevant tools 1-2 weeks ahead of HN. A URL mentioned by 3+
newsletters in the same week is a strong "this matters" signal.

Logic:
- Pull each feed (httpx + feedparser).
- Filter entries to the last `lookback_days`.
- Regex-extract URLs from entry HTML bodies.
- Aggregate per URL: unique-newsletter count, first_seen, last_seen.
- Surface as NewsletterSignal — feeds into run.py both as candidate
  terms and as a top-level snapshot field.

Curated feed list lives at data/newsletters.json. The list is empty by
default; populate with `{name, feed_url}` pairs. Without entries the
fetcher returns [] silently.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import feedparser
import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

NEWSLETTERS_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "newsletters.json"
)
NL_REQUEST_INTERVAL_SECONDS = 0.5
NL_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"

_URL_ANCHOR_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)
_URL_BARE_RE = re.compile(r"(https?://[^\s<>\"']+)")


class NewsletterEntry(BaseModel):
    title: str
    link: str
    published_at: datetime
    body_html: str


class NewsletterSignal(BaseModel):
    url: str
    unique_newsletters_count: int
    newsletters: list[str]
    first_seen: datetime
    last_seen: datetime


def _parse_pubdate(raw: Any, fallback: datetime) -> datetime:
    """feedparser exposes published as a struct_time on `published_parsed`."""
    if raw is not None:
        try:
            return datetime(*raw[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return fallback


def parse_feed(xml_text: str) -> list[NewsletterEntry]:
    feed = feedparser.parse(xml_text)
    now = datetime.now(tz=timezone.utc)
    out: list[NewsletterEntry] = []
    for entry in feed.entries:
        published = _parse_pubdate(entry.get("published_parsed"), now)
        body = entry.get("summary") or entry.get("description") or ""
        out.append(
            NewsletterEntry(
                title=entry.get("title", "").strip(),
                link=entry.get("link", ""),
                published_at=published,
                body_html=body,
            )
        )
    return out


def extract_urls_from_html(html: str) -> list[str]:
    urls: set[str] = set()
    for match in _URL_ANCHOR_RE.findall(html):
        urls.add(match)
    # The bare-URL regex catches text URLs that aren't inside anchors but
    # would also match the href values already captured; the set dedupes.
    for match in _URL_BARE_RE.findall(html):
        # strip trailing punctuation that often follows bare URLs
        urls.add(match.rstrip(".,);!?"))
    return sorted(urls)


def aggregate_from_xml(
    feeds: dict[str, str],
    *,
    today: datetime,
    lookback_days: int = 14,
) -> list[NewsletterSignal]:
    """Aggregate cross-mentions across pre-fetched feed XML payloads."""
    cutoff = today - timedelta(days=lookback_days)
    per_url: dict[str, dict[str, Any]] = {}
    for newsletter_name, xml in feeds.items():
        entries = parse_feed(xml)
        for entry in entries:
            if entry.published_at < cutoff or entry.published_at > today:
                continue
            for url in extract_urls_from_html(entry.body_html):
                bucket = per_url.setdefault(
                    url,
                    {
                        "newsletters": set(),
                        "first_seen": entry.published_at,
                        "last_seen": entry.published_at,
                    },
                )
                bucket["newsletters"].add(newsletter_name)
                if entry.published_at < bucket["first_seen"]:
                    bucket["first_seen"] = entry.published_at
                if entry.published_at > bucket["last_seen"]:
                    bucket["last_seen"] = entry.published_at
    return [
        NewsletterSignal(
            url=url,
            unique_newsletters_count=len(b["newsletters"]),
            newsletters=sorted(b["newsletters"]),
            first_seen=b["first_seen"],
            last_seen=b["last_seen"],
        )
        for url, b in per_url.items()
    ]


def load_curated_feed_list(path: Path = NEWSLETTERS_CONFIG_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@with_retry(attempts=3, base_delay=1.0)
def _fetch_one(url: str) -> str:
    headers = {"User-Agent": NL_USER_AGENT}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def fetch_newsletter_signals(
    feeds: Optional[Iterable[dict[str, str]]] = None,
    *,
    lookback_days: int = 14,
    today: Optional[datetime] = None,
) -> list[NewsletterSignal]:
    """Live fetch + aggregate. `feeds` defaults to the curated config file."""
    feeds_list = list(feeds) if feeds is not None else load_curated_feed_list()
    if not feeds_list:
        return []
    today = today or datetime.now(tz=timezone.utc)
    fetched: dict[str, str] = {}
    for feed in feeds_list:
        url = feed["feed_url"]
        name = feed.get("name", url)
        try:
            fetched[name] = _fetch_one(url)
        except Exception:
            continue
        time.sleep(NL_REQUEST_INTERVAL_SECONDS)
    return aggregate_from_xml(fetched, today=today, lookback_days=lookback_days)
