"""Generic write-through corpus cache for fetched source data.

Mirrors the data/digg_ai_corpus.json pattern: a cumulative per-source JSON
file keyed by stable doc_id, with observations[] preserving when each
record was last seen. The corpus is the single mechanism the pipeline
uses to NOT lose fetch work when:

- The downstream pipeline crashes after a paid fetch (Perplexity, Grok).
- A free fetcher rate-limits or returns empty on a given run.
- A future analysis wants a multi-day window (the orchestrator only
  reads today's snapshot otherwise).

Each fetcher gets two thin wrappers in its own module (see
pipeline.fetch.digg as the original reference): an `update_corpus(items)`
that calls this module, and a `load_recent_corpus(lookback_days)` that
reads it back. The actual JSON shape lives here so the format stays
consistent across sources.

Corpus shape (per source):

    {
        "<doc_id>": {
            "first_seen": "<isoformat>",
            "observations": ["<isoformat>", ...],
            "data": { ... raw item payload ... }
        },
        ...
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT_DATA = Path(__file__).resolve().parent.parent / "data"


def corpus_path(source: str) -> Path:
    return ROOT_DATA / f"{source}_corpus.json"


def _read_corpus(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def update_corpus(
    source: str,
    items: Iterable[dict[str, Any]],
    *,
    id_field: str,
    now: datetime | None = None,
    path: Path | None = None,
) -> int:
    """Merge `items` into the on-disk corpus for `source`.

    Each item is keyed by `item[id_field]`. New items get a fresh
    observations list; existing items append a new observation timestamp
    and refresh the stored payload to the latest version. Returns the
    number of items processed (new + updated).
    """
    p = path or corpus_path(source)
    now_dt = now or datetime.now(tz=timezone.utc)
    now_iso = now_dt.isoformat()
    corpus = _read_corpus(p)
    processed = 0
    for item in items:
        raw_id = item.get(id_field)
        if raw_id is None or raw_id == "":
            continue
        doc_id = str(raw_id)
        if doc_id in corpus:
            obs = corpus[doc_id].setdefault("observations", [])
            obs.append(now_iso)
            corpus[doc_id]["data"] = item
        else:
            corpus[doc_id] = {
                "first_seen": now_iso,
                "observations": [now_iso],
                "data": item,
            }
        processed += 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(corpus, default=str, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return processed


def load_recent_corpus(
    source: str,
    *,
    lookback_days: int,
    now: datetime | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return the raw payloads (item["data"]) of corpus entries whose most
    recent observation falls within the lookback window.
    """
    p = path or corpus_path(source)
    corpus = _read_corpus(p)
    if not corpus:
        return []
    now_dt = now or datetime.now(tz=timezone.utc)
    cutoff = now_dt - timedelta(days=lookback_days)
    out: list[dict[str, Any]] = []
    for entry in corpus.values():
        observations = entry.get("observations") or []
        if not observations:
            continue
        try:
            last_dt = max(datetime.fromisoformat(o) for o in observations)
        except (TypeError, ValueError):
            continue
        if last_dt >= cutoff:
            out.append(entry.get("data") or {})
    return out
