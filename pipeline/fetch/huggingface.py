"""Fetch trending Hugging Face Hub models.

Audit item 3.1 — Theme 7 follow-up: HF Hub is 1-3 weeks ahead of GitHub
and arXiv for AI-tool launches that solo creators actually adopt. No
auth required; the public /api/models endpoint serves trending sorted
by an internal momentum score.

Endpoint: GET https://huggingface.co/api/models?sort=trendingScore&limit=100
Each item carries `likes`, `downloads`, `tags`, `pipeline_tag` — all
used for term extraction and per-term aggregation in run.py.

Scope deviation (surfaced): the audit doc named huggingface_spaces_7d
as a separate count. That requires a second fetch against /api/spaces
and a different shape. v1 ships /api/models only; spaces stays at 0.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from pipeline.fetch._retry import with_retry

HF_API_URL = "https://huggingface.co/api/models"
HF_DEFAULT_LIMIT = 100
HF_REQUEST_INTERVAL_SECONDS = 1.0
HF_USER_AGENT = "ai-alpha-radar/0.1 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"


class HFModel(BaseModel):
    id: str  # "org/name"
    likes: int = 0
    downloads: int = 0
    trending_score: float = 0.0
    tags: list[str] = []
    pipeline_tag: Optional[str] = None
    library_name: Optional[str] = None
    private: bool = False
    # v0.2.0 — download velocity vs the 7-day-prior snapshot. None until
    # the pipeline has a prior snapshot to delta against.
    downloads_7d_delta: Optional[int] = None
    warming_up: bool = True


def parse_search_response(payload: list[dict[str, Any]]) -> list[HFModel]:
    """Parse a /api/models response list, skipping private entries."""
    out: list[HFModel] = []
    for item in payload:
        if item.get("private"):
            continue
        if not item.get("id"):
            continue
        out.append(
            HFModel(
                id=item["id"],
                likes=int(item.get("likes") or 0),
                downloads=int(item.get("downloads") or 0),
                trending_score=float(item.get("trendingScore") or 0.0),
                tags=list(item.get("tags") or []),
                pipeline_tag=item.get("pipeline_tag") or None,
                library_name=item.get("library_name") or None,
                private=False,
            )
        )
    return out


def model_name(m: HFModel) -> str:
    """Extract the model-name portion of `org/name`."""
    return m.id.rsplit("/", 1)[-1]


def model_text(m: HFModel) -> str:
    """Single doc string for term extraction — name + tags + pipeline_tag."""
    parts = [model_name(m).replace("-", " ").replace(".", " "), " ".join(m.tags)]
    if m.pipeline_tag:
        parts.append(m.pipeline_tag.replace("-", " "))
    return "\n".join(parts)


def compute_download_velocity(
    today: list[HFModel], *, prior_downloads: dict[str, int]
) -> list[HFModel]:
    """v0.2.0 — annotate each model with downloads_7d_delta using prior map.
    Models not in the prior snapshot stay warming_up=True with delta=None.
    """
    annotated: list[HFModel] = []
    for m in today:
        if m.id in prior_downloads:
            annotated.append(
                m.model_copy(update={
                    "downloads_7d_delta": m.downloads - prior_downloads[m.id],
                    "warming_up": False,
                })
            )
        else:
            annotated.append(m)
    return annotated


def load_prior_download_map(snapshot_path: Path) -> dict[str, int]:
    """v0.2.0 — read a prior snapshot's meta.hf_downloads. Returns {} if absent."""
    if not snapshot_path.exists():
        return {}
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("meta", {}).get("hf_downloads", {})


@with_retry(attempts=3, base_delay=1.0)
def fetch_trending_models(
    limit: int = HF_DEFAULT_LIMIT,
    *,
    snapshots_dir: Optional[Path] = None,
    lookback_days: int = 7,
) -> list[HFModel]:
    """Live fetch of trending HF models. No auth.

    If `snapshots_dir` is provided, attach downloads_7d_delta against the
    snapshot from `lookback_days` ago (defaults to 7d). Without it the
    velocity layer stays None / warming_up=True, preserving legacy callers.
    """
    headers = {"User-Agent": HF_USER_AGENT}
    params = {"sort": "trendingScore", "direction": -1, "limit": limit, "full": True}
    with httpx.Client(timeout=30, headers=headers) as client:
        response = client.get(HF_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
    time.sleep(HF_REQUEST_INTERVAL_SECONDS)
    today = parse_search_response(data)

    if snapshots_dir is not None:
        prior_date = (datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).date()
        prior = load_prior_download_map(snapshots_dir / f"{prior_date.isoformat()}.json")
        if prior:
            today = compute_download_velocity(today, prior_downloads=prior)
    return today


if __name__ == "__main__":
    items = fetch_trending_models(limit=20)
    print(f"fetched {len(items)} trending models")
    for m in items[:5]:
        print(f"  - {m.id} likes={m.likes} downloads={m.downloads} pipeline={m.pipeline_tag}")
