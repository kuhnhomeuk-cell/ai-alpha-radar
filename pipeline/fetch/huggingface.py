"""Fetch trending models and spaces from the public HuggingFace Hub API.

Per Path C buildout — model-adoption signal that AlphaSignal showcases
as a first-class section. The HF Hub API is read-only without auth and
generous with rate limits.

Download-velocity strategy mirrors github.py: HF only exposes lifetime
`downloads`, so we snapshot today's count per model into meta and compute
`downloads_7d_delta` against the 7-day-prior snapshot on the next run.
Day-1 reality: `warming_up=True` until snapshots accumulate.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel

HF_MODELS_URL = "https://huggingface.co/api/models"
HF_REQUEST_TIMEOUT = 20.0
HF_REQUEST_INTERVAL_SECONDS = 0.5
HF_USER_AGENT = "ai-alpha-radar/0.2 (+https://github.com/kuhnhomeuk-cell/ai-alpha-radar)"

# pipeline_tag values that signal AI/ML model relevance.
AI_PIPELINE_TAGS: frozenset[str] = frozenset({
    "text-generation",
    "text2text-generation",
    "image-text-to-text",
    "automatic-speech-recognition",
    "text-to-speech",
    "text-to-image",
    "image-to-image",
    "image-to-text",
    "visual-question-answering",
    "feature-extraction",
    "sentence-similarity",
    "fill-mask",
    "translation",
    "summarization",
    "question-answering",
    "text-classification",
    "token-classification",
    "zero-shot-classification",
    "conversational",
})


class HFModel(BaseModel):
    id: str  # e.g. "meta-llama/Llama-3-70B"
    author: Optional[str] = None
    downloads: int
    likes: int
    last_modified: Optional[datetime] = None
    pipeline_tag: Optional[str] = None
    library_name: Optional[str] = None
    tags: list[str] = []
    url: str
    downloads_7d_delta: Optional[int] = None
    warming_up: bool = True


def _parse_iso(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_models_response(payload: list[dict[str, Any]]) -> list[HFModel]:
    """Parse a HF /api/models JSON array into HFModel objects.

    Filters to AI-relevant pipeline_tags. Missing fields are tolerated:
    HF's response shape varies across model types (some have no
    library_name, some have empty tags, etc.).
    """
    models: list[HFModel] = []
    for item in payload:
        model_id = item.get("id") or item.get("modelId")
        if not model_id:
            continue
        pipeline_tag = item.get("pipeline_tag")
        if pipeline_tag and pipeline_tag not in AI_PIPELINE_TAGS:
            # If there's a pipeline_tag and it's outside our AI set, skip.
            # A None pipeline_tag is allowed (some models don't declare one
            # but are still AI-relevant via their tags).
            continue
        models.append(
            HFModel(
                id=model_id,
                author=item.get("author"),
                downloads=int(item.get("downloads") or 0),
                likes=int(item.get("likes") or 0),
                last_modified=_parse_iso(item.get("lastModified")),
                pipeline_tag=pipeline_tag,
                library_name=item.get("library_name"),
                tags=list(item.get("tags") or []),
                url=f"https://huggingface.co/{model_id}",
            )
        )
    return models


def compute_download_velocity(
    today: list[HFModel], *, prior_downloads: dict[str, int]
) -> list[HFModel]:
    """Annotate each model with downloads_7d_delta and warming_up using prior map."""
    annotated: list[HFModel] = []
    for m in today:
        if m.id in prior_downloads:
            delta = m.downloads - prior_downloads[m.id]
            annotated.append(
                m.model_copy(update={"downloads_7d_delta": delta, "warming_up": False})
            )
        else:
            annotated.append(m)
    return annotated


def load_prior_download_map(snapshot_path: Path) -> dict[str, int]:
    """Read a previous snapshot's meta.hf_downloads map. Returns {} if missing."""
    if not snapshot_path.exists():
        return {}
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return data.get("meta", {}).get("hf_downloads", {})


def fetch_trending_models(
    *,
    limit: int = 50,
    sort: str = "downloads",
    snapshots_dir: Optional[Path] = None,
    lookback_days: int = 7,
) -> list[HFModel]:
    """Live HF Hub query for top trending AI models.

    Defaults to sorting by lifetime downloads descending. If snapshots_dir
    is supplied, attaches downloads_7d_delta against the 7-day-prior
    snapshot's meta.hf_downloads map.
    """
    params = {
        "sort": sort,
        "direction": "-1",
        "limit": limit,
        "full": "true",
    }
    headers = {"User-Agent": HF_USER_AGENT}
    with httpx.Client(timeout=HF_REQUEST_TIMEOUT, headers=headers) as client:
        response = client.get(HF_MODELS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    time.sleep(HF_REQUEST_INTERVAL_SECONDS)

    today = parse_models_response(payload)

    prior: dict[str, int] = {}
    if snapshots_dir is not None:
        prior_date = (datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)).date()
        prior = load_prior_download_map(snapshots_dir / f"{prior_date.isoformat()}.json")

    return compute_download_velocity(today, prior_downloads=prior)


if __name__ == "__main__":
    models = fetch_trending_models(snapshots_dir=Path("public/snapshots"))
    warming = sum(1 for m in models if m.warming_up)
    print(
        f"fetched {len(models)} trending AI HF models, "
        f"{warming} warming_up (no prior snapshot yet)"
    )
    for m in sorted(models, key=lambda m: -m.downloads)[:5]:
        delta = "warming" if m.warming_up else f"+{m.downloads_7d_delta}"
        print(f"  - [{m.downloads:>10}dl {m.likes:>5}♥ {delta}] {m.id} ({m.pipeline_tag})")
    if len(models) < 5:
        sys.exit(1)
