"""Wave 5 — Perplexity Sonar pain-point enrichment fetcher.

For a given topic, asks Sonar what solo creators are struggling with and
returns 3-5 ranked PainPoints with source citations.

Design notes
- Endpoint: POST https://api.perplexity.ai/chat/completions
- Model: 'sonar' (the cheap online-search variant; ~$0.005 search fee + token
  cost per request, total ~1¢/call)
- Auth: Bearer ${PERPLEXITY_API_KEY}
- Failure mode: a missing key, persistent HTTP failure, or malformed payload
  all downgrade to ([], 0) — pain points are enrichment, not a hard input.
  A failing trend must not crash the snapshot.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from typing import Any, Optional

import httpx

from pipeline.fetch._retry import with_retry
from pipeline.models import PainPoint

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"
DEFAULT_MAX_PAIN_POINTS = 5
DEFAULT_TIMEOUT_SECONDS = 30

# Sonar pricing (verified 2026-05-15): $1/1M input + $1/1M output tokens
# plus a $5/1000-request search fee. The search fee is the dominant cost
# for short pain-point queries.
_SEARCH_FEE_CENTS = 0.5  # $0.005/request × 100 cents/dollar
_TOKEN_COST_PER_M_CENTS = 100.0  # $1/1M tokens × 100 cents/dollar

# Sonar occasionally wraps JSON in ```json fences despite the instruction;
# this matches an opening fence at the start of a line and a closing fence
# at the end.
_CODEFENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_PROMPT_TEMPLATE = (
    'What are solo creators struggling with around "{topic}"? '
    "List 3-5 unanswered questions, missing tutorials, or recurring complaints "
    "from the past 30 days. Return JSON only (no prose) as an array of objects "
    'with these exact fields: "text" (the question or complaint, one sentence), '
    '"source_url" (URL of evidence), "source_title" (page title). '
    "Cite real, recent sources only. "
    "Order the array from most important to least important."
)


def _strip_codefence(content: str) -> str:
    return _CODEFENCE_RE.sub("", content).strip()


def parse_response(payload: dict[str, Any]) -> list[PainPoint]:
    """Extract PainPoints from a Sonar chat-completions response.

    Robust to malformed content: returns [] rather than raising. The first
    choice's message content is parsed as JSON; non-list payloads and items
    missing any required field are skipped.
    """
    choices = payload.get("choices") or []
    if not choices:
        return []
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if not isinstance(content, str) or not content:
        return []
    cleaned = _strip_codefence(content)
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out: list[PainPoint] = []
    rank = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        source_url = item.get("source_url")
        source_title = item.get("source_title")
        if not (
            isinstance(text, str)
            and isinstance(source_url, str)
            and isinstance(source_title, str)
        ):
            continue
        if not (text and source_url and source_title):
            continue
        rank += 1
        out.append(
            PainPoint(
                text=text,
                source_url=source_url,
                source_title=source_title,
                rank=rank,
            )
        )
    return out


def estimate_cost_cents(payload: dict[str, Any]) -> int:
    """Approximate per-call cost in cents from the response's `usage` block.

    Floor of 1 cent — the $0.005 search fee alone rounds up to 1¢, and
    every successful call incurs it.
    """
    usage = payload.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or 0
    total_tokens = prompt_tokens + completion_tokens
    token_cents = (total_tokens / 1_000_000.0) * _TOKEN_COST_PER_M_CENTS
    raw = _SEARCH_FEE_CENTS + token_cents
    return max(1, math.ceil(raw))


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


def _build_body(topic: str) -> dict[str, Any]:
    return {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {"role": "user", "content": _PROMPT_TEMPLATE.format(topic=topic)},
        ],
    }


def fetch_pain_points(
    topic: str,
    *,
    max_pain_points: int = DEFAULT_MAX_PAIN_POINTS,
    api_key: Optional[str] = None,
) -> tuple[list[PainPoint], int]:
    """Public entrypoint. Returns `(pain_points, cost_cents)`.

    Failure modes (all downgrade to `([], 0)`):
    - `PERPLEXITY_API_KEY` missing
    - persistent HTTP failure (after _post's 3 attempts)

    Token-billing exception: if the API call succeeded but the payload was
    malformed, we still charge `estimate_cost_cents` because the request
    incurred a real search fee on Perplexity's side.
    """
    key = api_key or os.environ.get("PERPLEXITY_API_KEY")
    if not key:
        print(
            "perplexity: PERPLEXITY_API_KEY missing — skipping pain-point enrichment",
            file=sys.stderr,
        )
        return [], 0
    body = _build_body(topic)
    try:
        payload = _post(PERPLEXITY_API_URL, body=body, key=key)
    except httpx.HTTPError as e:
        print(
            f"perplexity: {type(e).__name__} on topic={topic!r}; skipping",
            file=sys.stderr,
        )
        return [], 0
    points = parse_response(payload)
    cost = estimate_cost_cents(payload)
    return points[:max_pain_points], cost
