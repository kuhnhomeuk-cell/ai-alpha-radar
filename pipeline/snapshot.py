"""Snapshot writer — produces public/data.json + public/snapshots/{date}.json.

Per BACKEND_BUILD §7 Step 12. Both files are the same Snapshot JSON; data.json
is "the current" (what the frontend reads), and snapshots/YYYY-MM-DD.json is
the dated archive (the Star Log demo proof). Idempotent — re-running on the
same day overwrites.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from pipeline.models import Snapshot

DEFAULT_PUBLIC_DIR = Path("public")
DATA_JSON = "data.json"
SNAPSHOTS_SUBDIR = "snapshots"


def write_snapshot(snap: Snapshot, *, public_dir: Path = DEFAULT_PUBLIC_DIR) -> Path:
    """Write the snapshot to public/data.json AND public/snapshots/{date}.json.

    Returns the path to the dated snapshot file.
    """
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / SNAPSHOTS_SUBDIR).mkdir(parents=True, exist_ok=True)

    payload = snap.model_dump_json(indent=2)

    current = public_dir / DATA_JSON
    current.write_text(payload, encoding="utf-8")

    dated = public_dir / SNAPSHOTS_SUBDIR / f"{snap.snapshot_date.isoformat()}.json"
    dated.write_text(payload, encoding="utf-8")
    return dated


def read_prior_snapshot(
    snapshot_date: date, *, public_dir: Path = DEFAULT_PUBLIC_DIR
) -> Optional[Snapshot]:
    """Load a previously-written snapshot from public/snapshots/{date}.json."""
    path = public_dir / SNAPSHOTS_SUBDIR / f"{snapshot_date.isoformat()}.json"
    if not path.exists():
        return None
    return Snapshot.model_validate_json(path.read_text(encoding="utf-8"))
