"""Replicate trending models — run-count signal.

Audit item 3.5. Replicate's per-model run_count is the closest thing
to a real "users adopting this in production" signal in the open-source
AI tooling ecosystem. 1-2 weeks ahead of GitHub traffic for hosted-
inference models.

API: GET https://api.replicate.com/v1/models (Bearer auth).
Token: REPLICATE_API_KEY.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional, Sequence

import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

REPLICATE_API_URL = "https://api.replicate.com/v1/models"
REPLICATE_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"
REPLICATE_REQUEST_INTERVAL_SECONDS = 1.0
DEFAULT_RUN_COUNTS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "replicate_run_counts.json"
)


class ReplicateModel(BaseModel):
    owner: str
    name: str
    description: Optional[str] = None
    visibility: str
    run_count: int


def parse_response(payload: dict[str, Any]) -> list[ReplicateModel]:
    out: list[ReplicateModel] = []
    for r in payload.get("results", []) or []:
        if r.get("visibility") != "public":
            continue
        out.append(
            ReplicateModel(
                owner=r.get("owner", ""),
                name=r.get("name", ""),
                description=r.get("description") or None,
                visibility=r.get("visibility", "private"),
                run_count=int(r.get("run_count") or 0),
            )
        )
    return out


def run_count_deltas(
    today: dict[str, int], prior: dict[str, int]
) -> dict[str, int]:
    """Today − prior, clipped to ≥0. New models contribute their full count."""
    out: dict[str, int] = {}
    for name, count in today.items():
        out[name] = max(0, count - prior.get(name, 0))
    return out


def runs_per_term(
    models: Sequence[ReplicateModel], *, terms: Sequence[str]
) -> dict[str, int]:
    out: dict[str, int] = {t: 0 for t in terms}
    docs = [(m.name + " " + (m.description or "")).lower() for m in models]
    runs = [m.run_count for m in models]
    for t in terms:
        needle = t.lower()
        out[t] = sum(r for d, r in zip(docs, runs) if needle in d)
    return out


def load_prior_run_counts(path: Path = DEFAULT_RUN_COUNTS_PATH) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_run_counts(
    counts: dict[str, int], path: Path = DEFAULT_RUN_COUNTS_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts, indent=2), encoding="utf-8")


@with_retry(attempts=3, base_delay=1.0)
def _get(url: str, token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Token {token}",
        "User-Agent": REPLICATE_USER_AGENT,
        "Accept": "application/json",
    }
    with httpx.Client(timeout=30, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def fetch_trending(
    *, max_pages: int = 3
) -> list[ReplicateModel]:  # pragma: no cover — live API
    """Walk Replicate's paginated model list. Returns [] if no token.

    Failure surfacing: a missing REPLICATE_API_KEY is the dominant
    production cause of "0 models" — log it to stderr so the run output
    explains the empty source. HTTP errors mid-pagination also log but
    still return whatever pages succeeded.
    """
    import sys

    token = os.environ.get("REPLICATE_API_KEY", "").strip()
    if not token:
        print(
            "replicate: REPLICATE_API_KEY missing — skipping model fetch",
            file=sys.stderr,
        )
        return []
    url: Optional[str] = REPLICATE_API_URL
    out: list[ReplicateModel] = []
    pages = 0
    while url and pages < max_pages:
        try:
            payload = _get(url, token)
        except Exception as e:
            print(
                f"replicate: {type(e).__name__} on page {pages}; "
                f"returning partial result ({len(out)} models so far)",
                file=sys.stderr,
            )
            break
        out.extend(parse_response(payload))
        url = payload.get("next")
        pages += 1
        time.sleep(REPLICATE_REQUEST_INTERVAL_SECONDS)
    return out


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv(".env.local", override=True)
    token_set = bool(os.environ.get("REPLICATE_API_KEY", "").strip())
    print(f"auth: {'REPLICATE_API_KEY set' if token_set else 'UNAUTHENTICATED (skip)'}")
    models = fetch_trending(max_pages=2)
    print(f"fetched {len(models)} public models across ≤2 pages")
    # Sort descending by run_count and show the head — that's the signal.
    top = sorted(models, key=lambda m: m.run_count, reverse=True)[:5]
    for m in top:
        print(f"  - [{m.run_count:>12,} runs] {m.owner}/{m.name}")
    if token_set and not models:
        sys.exit(1)
