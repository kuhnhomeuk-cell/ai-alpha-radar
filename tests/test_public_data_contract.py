"""Validate committed public JSON artifacts against the frontend contract."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.models import Snapshot

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_data_json_and_snapshots_validate_against_snapshot_model() -> None:
    paths = [PUBLIC / "data.json", *sorted((PUBLIC / "snapshots").glob("*.json"))]
    assert paths, "expected checked-in public data artifacts"
    for path in paths:
        snap = Snapshot.model_validate(_load_json(path))
        assert snap.trends or snap.meta.get("empty") is True


def test_public_data_json_matches_its_dated_snapshot() -> None:
    current = _load_json(PUBLIC / "data.json")
    dated_snapshot = PUBLIC / "snapshots" / f"{current['snapshot_date']}.json"
    assert dated_snapshot.exists()
    assert current == _load_json(dated_snapshot)


def test_convergence_false_events_are_empty_in_public_data() -> None:
    snap = Snapshot.model_validate(_load_json(PUBLIC / "data.json"))
    for trend in snap.trends:
        if trend.convergence.detected:
            continue
        assert trend.convergence.sources_hit == []
        assert trend.convergence.window_hours == 0
        assert trend.convergence.first_appearance == {}
