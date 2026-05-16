"""Wave 6 — xAI Grok X Search signal per trend.

For a given trend keyword, asks Grok (via the xAI Responses API) to surface
recent X posts mentioning the topic within a configurable date window. The
fetcher returns the X-mention count, which populates `SourceCounts.x_posts_7d`
on the matching Trend — finally filling a schema slot that has been
hardcoded to 0 since v0.1.0.

Design notes
- Endpoint: POST https://api.x.ai/v1/responses
- Model: grok-4.3 (xAI's reasoning model that supports `search_parameters`)
- Auth: Bearer ${XAI_API_KEY}
- Cost: per-call cost reported by xAI as `usage.cost_in_usd_ticks`
  (100,000,000 ticks = $1). We compute cents and report up to run.py so the
  existing --max-cost-cents budget gate can throttle.
- Failure mode: any missing-key / persistent HTTP / parse failure downgrades
  to (0, 0) — X signal is enrichment, not a hard input.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import date, timedelta
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from pipeline.fetch._retry import with_retry

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
# grok-4.3 is the non-reasoning sibling of grok-4.20-reasoning at the same
# per-token price ($1.25 in / $2.50 out per 1M) but without the reasoning
# token burn. Faster too. See §5 sign-off in the Wave 6 PR.
GROK_MODEL = "grok-4.3"
DEFAULT_DAYS_BACK = 7
DEFAULT_MAX_SEARCH_RESULTS = 25
# Cap server-side tool rounds so a curious model can't fire 4+ x_search
# calls per topic (the §5-1 run did, at $1+/call). 2 is enough for one
# pass of the keyword search + one refinement.
DEFAULT_MAX_TOOL_CALLS = 2
# x_search latency runs 30-90s; pad to 3 minutes for the long tail.
DEFAULT_TIMEOUT_SECONDS = 180

# xAI bills in "USD ticks" where 100M ticks = $1.00. So 1¢ = 1_000_000 ticks.
_TICKS_PER_CENT = 1_000_000

# Hosts that count as an X / Twitter post for the citations filter.
_X_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"})

_PROMPT_TEMPLATE = (
    'How much are creators talking about "{topic}" on X in the past {days} days? '
    "Cite up to 20 representative recent X posts (links). "
    "Keep the prose brief — the value is in the citations."
)


def _is_x_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return False
    return host in _X_HOSTS


def _collect_urls(node: Any, out: set[str]) -> None:
    """Walk an arbitrary nested payload, collecting any string that looks
    like an HTTP(S) URL. Used to find X-post links wherever xAI nests them
    (top-level `citations`, inline `output[].content[].annotations[].url`,
    `output[].content[].annotations[].cited_url`, etc.)."""
    if isinstance(node, str):
        if node.startswith("http://") or node.startswith("https://"):
            out.add(node)
        return
    if isinstance(node, list):
        for item in node:
            _collect_urls(item, out)
        return
    if isinstance(node, dict):
        for v in node.values():
            _collect_urls(v, out)


def count_x_mentions(payload: dict[str, Any]) -> int:
    """Extract X-post mention count from a Grok Responses API payload.

    The Responses API can put X URLs in several places depending on whether
    the model used `x_search` as a server-side tool or returned inline
    annotations:

    1. Top-level `response.citations` (legacy chat-completions shape)
    2. `response.output[].content[].annotations[].url` (inline cite shape)
    3. `response.output[]` whose `type` is `x_search_result` (tool shape)

    We deep-walk the payload, deduplicate every URL found, and count those
    whose host is x.com / twitter.com. Falls back to
    `usage.num_sources_used` only when no URLs are found at all.
    """
    urls: set[str] = set()
    _collect_urls(payload, urls)
    if urls:
        return sum(1 for u in urls if _is_x_url(u))
    usage = payload.get("usage") or {}
    return int(usage.get("num_sources_used") or 0)


def estimate_cost_cents(payload: dict[str, Any]) -> int:
    """Convert `usage.cost_in_usd_ticks` to cents, ceiling, floor of 1¢."""
    usage = payload.get("usage") or {}
    ticks = usage.get("cost_in_usd_ticks")
    if not ticks:
        return 1  # API was called; charge minimum
    return max(1, math.ceil(int(ticks) / _TICKS_PER_CENT))


@with_retry(attempts=3, base_delay=1.0, max_delay=30.0)
def _post(url: str, *, body: dict[str, Any], key: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
        resp = client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()


def _build_body(
    topic: str,
    *,
    days: int = DEFAULT_DAYS_BACK,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Build the Responses API request body.

    xAI deprecated the `search_parameters` field on /v1/responses (returns
    410 Gone). The current way to enable X-only search is via the `tools`
    array with `{"type": "x_search"}` plus tool-specific date filters.
    """
    today_d = today or date.today()
    from_date = (today_d - timedelta(days=days)).isoformat()
    to_date = today_d.isoformat()
    return {
        "model": GROK_MODEL,
        "input": [
            {"role": "user", "content": _PROMPT_TEMPLATE.format(topic=topic, days=days)}
        ],
        "tools": [
            {
                "type": "x_search",
                "from_date": from_date,
                "to_date": to_date,
            }
        ],
        # Hard cap on server-side tool rounds. Without this, the model can
        # chain 4+ x_search calls and balloon the bill (§5-1 hit $4.87).
        "max_tool_calls": DEFAULT_MAX_TOOL_CALLS,
    }


def fetch_x_mention_count(
    topic: str,
    *,
    days: int = DEFAULT_DAYS_BACK,
    api_key: Optional[str] = None,
) -> tuple[int, int]:
    """Public entrypoint. Returns `(x_mention_count, cost_cents)`.

    Failure modes (all downgrade to `(0, 0)`):
    - `XAI_API_KEY` missing
    - persistent HTTP failure (after `_post`'s 3 attempts)
    """
    key = api_key or os.environ.get("XAI_API_KEY")
    if not key:
        print(
            "grok: XAI_API_KEY missing — skipping X-mention enrichment",
            file=sys.stderr,
        )
        return 0, 0
    body = _build_body(topic, days=days)
    try:
        payload = _post(XAI_RESPONSES_URL, body=body, key=key)
    except httpx.HTTPError as e:
        print(
            f"grok: {type(e).__name__} on topic={topic!r}; skipping",
            file=sys.stderr,
        )
        return 0, 0
    return count_x_mentions(payload), estimate_cost_cents(payload)
