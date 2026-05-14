"""TDD for pipeline.fetch.replicate (audit 3.5)."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.fetch import replicate

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "replicate_sample.json"


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_response_extracts_public_models() -> None:
    models = replicate.parse_response(_load())
    assert len(models) == 2
    by_name = {m.name: m for m in models}
    assert "flux-schnell" in by_name
    assert by_name["flux-schnell"].run_count == 152_300_000


def test_parse_response_filters_private() -> None:
    models = replicate.parse_response(_load())
    assert all(m.visibility == "public" for m in models)


def test_run_count_deltas_against_prior() -> None:
    today = {"flux-schnell": 1_000_000, "llama-3-70b": 500_000, "new-model": 10_000}
    prior = {"flux-schnell": 900_000, "llama-3-70b": 600_000}
    deltas = replicate.run_count_deltas(today, prior)
    assert deltas["flux-schnell"] == 100_000
    # Drop is clipped to 0 (deltas count growth, not regression).
    assert deltas["llama-3-70b"] == 0
    # Brand-new model — full count counts as growth.
    assert deltas["new-model"] == 10_000


def test_runs_per_term_aggregates_matches() -> None:
    models = replicate.parse_response(_load())
    counts = replicate.runs_per_term(models, terms=["flux", "llama"])
    assert counts["flux"] == 152_300_000
    assert counts["llama"] == 48_900_000
