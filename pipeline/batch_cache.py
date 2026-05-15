"""Local-file batch idempotency cache (audit 4.2).

Wraps a single JSON file (`data/.batch_state.json`) keyed by a stable
sha256 of an Anthropic Messages Batch request list. Two purposes:

  1. If the daily pipeline crashes after a batch has been submitted
     but before its results were consumed, the next run polls the
     existing batch instead of creating a duplicate.
  2. If the daily pipeline re-runs on the same day with the same card
     set, completed results are returned from cache — no second
     submission.

The audit lists Cloudflare KV as the preferred backend, with a local
file as the fallback. The pipeline runs in CI without KV credentials,
so we ship the file backend; if Workers KV access opens up later the
get/set surface is small enough to swap.

Entries older than 24h are evicted on read (Anthropic batches expire
after ~24h server-side anyway).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


TTL_SECONDS = 24 * 60 * 60


def hash_requests(requests: list[dict[str, Any]]) -> str:
    """Stable hash of a batch request list.

    Keyed by (custom_id, prompt text) tuples sorted by custom_id so that
    two callers building the same logical batch in slightly different
    orders still collide deterministically.
    """
    canon = sorted(
        (r["custom_id"], r["params"]["messages"][0]["content"]) for r in requests
    )
    payload = json.dumps(canon, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BatchCache:
    """JSON-file backed cache. Single writer; never used concurrently."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except (json.JSONDecodeError, ValueError):
                # Corrupt cache file: ignore. Idempotency is best-effort.
                self._data = {}

    def get(self, key: str) -> Optional[dict[str, Any]]:
        entry = self._data.get(key)
        if entry is None:
            return None
        ts = entry.get("submitted_at")
        if ts:
            try:
                submitted = datetime.fromisoformat(ts)
                age = (datetime.now(tz=timezone.utc) - submitted).total_seconds()
                if age > TTL_SECONDS:
                    del self._data[key]
                    self._save()
                    return None
            except (ValueError, TypeError):
                pass
        return entry

    def set_batch_id(self, key: str, batch_id: str) -> None:
        """Record that this batch was submitted but not yet collected."""
        self._data[key] = {
            "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
            "batch_id": batch_id,
        }
        self._save()

    def set_results(
        self, key: str, batch_id: str, results: dict[str, dict[str, Any]]
    ) -> None:
        """Record both the batch id and the parsed result map."""
        self._data[key] = {
            "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
            "batch_id": batch_id,
            "results": results,
        }
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
