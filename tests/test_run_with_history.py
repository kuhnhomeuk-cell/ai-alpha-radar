"""History-aware orchestrator tests — covers Wave-1 item 1.1.

These tests pre-write dated snapshots under public_dir/snapshots/ then re-run the
orchestrator with the standard fixture inputs and assert that
velocity_score, velocity_acceleration, and sparkline_14d are populated rather
than the day-1 zero placeholders.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import predict, run, snapshot
from pipeline.fetch import arxiv, github, hackernews
from pipeline.models import (
    ConvergenceEvent,
    CreatorAngles,
    Prediction,
    RiskFlag,
    Snapshot,
    SourceCounts,
    Trend,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_papers():
    return arxiv.parse_atom_feed(
        (FIXTURES / "arxiv_sample.xml").read_text(encoding="utf-8"),
        categories=["cs.AI", "cs.LG", "cs.CL"],
    )


def _load_posts():
    return hackernews.parse_search_response(
        json.loads((FIXTURES / "hn_sample.json").read_text(encoding="utf-8"))
    )


def _load_repos():
    return github.parse_search_response(
        json.loads((FIXTURES / "github_sample.json").read_text(encoding="utf-8"))
    )


def _make_synthetic_trend(keyword: str, mentions: int) -> Trend:
    """Build a minimal valid Trend with the requested aggregate mentions."""
    return Trend(
        keyword=keyword,
        canonical_form=keyword,
        cluster_id=0,
        cluster_label="Synthetic",
        sources=SourceCounts(
            arxiv_30d=mentions,
            github_repos_7d=0,
            github_stars_7d=0,
            hn_posts_7d=0,
            hn_points_7d=0,
            semantic_scholar_citations_7d=0,
        ),
        velocity_score=1.0,
        velocity_acceleration=0.0,
        saturation=10.0,
        hidden_gem_score=0.5,
        builder_signal=0.5,
        lifecycle_stage="whisper",
        tbts=10,
        convergence=ConvergenceEvent(
            detected=False, sources_hit=[], window_hours=0, first_appearance={}
        ),
        summary="synthetic",
        summary_confidence="low",
        angles=CreatorAngles(hook="x", contrarian="x", tutorial="x", eli_creator="x"),
        risk=RiskFlag(
            breakout_likelihood="low",
            peak_estimate_days=None,
            risk_flag="synthetic",
            rationale="synthetic",
        ),
        prediction=Prediction(
            keyword=keyword,
            text="synthetic",
            filed_at=date(2026, 1, 1),
            target_date=date(2026, 12, 31),
            verdict="pending",
            lifecycle_at_filing="whisper",
            target_lifecycle="builder",
        ),
        sparkline_14d=[],
    )


def _write_prior_snapshot(
    public_dir: Path, snap_date: date, keyword: str, mentions: int
) -> None:
    snap = Snapshot(
        snapshot_date=snap_date,
        generated_at=datetime.combine(snap_date, datetime.min.time(), tzinfo=timezone.utc),
        trends=[_make_synthetic_trend(keyword, mentions)],
        demand_clusters=[],
        briefing=run._placeholder_briefing(),
        hit_rate=predict.compute_hit_rate([]),
        past_predictions=[],
        meta={"synthetic": True},
    )
    snapshot.write_snapshot(snap, public_dir=public_dir)


def test_run_with_no_history_emits_cold_start_meta(tmp_path: Path) -> None:
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.meta.get("cold_start") is True
    for t in snap.trends:
        assert t.velocity_score == 0.0
        assert t.velocity_acceleration == 0.0
        assert t.sparkline_14d == [0] * 14


def test_run_with_synthetic_history_computes_nonzero_velocity(tmp_path: Path) -> None:
    today = date(2026, 5, 13)
    # Pre-seed 14 days of synthetic snapshots so "llm" carries 5 mentions/day.
    for i in range(1, 15):
        _write_prior_snapshot(tmp_path, today - timedelta(days=i), "llm", 5)

    snap = run.main(
        today=today,
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.meta.get("cold_start") is False

    llm = next((t for t in snap.trends if t.keyword == "llm"), None)
    assert llm is not None, "expected 'llm' keyword in fixture-derived trends"
    assert llm.velocity_score > 0.0
    assert len(llm.sparkline_14d) == 14
    assert sum(llm.sparkline_14d) > 0
    # Yesterday's velocity was 1.0 (synthetic); today's is non-zero and different.
    assert llm.velocity_acceleration != 0.0


def test_run_recovers_from_corrupt_prior_snapshot(tmp_path: Path) -> None:
    today = date(2026, 5, 13)
    snaps_dir = tmp_path / "snapshots"
    snaps_dir.mkdir(parents=True, exist_ok=True)
    # Write a corrupt prior snapshot for yesterday.
    (snaps_dir / (today - timedelta(days=1)).isoformat()).with_suffix(".json").write_text(
        "{not valid json", encoding="utf-8"
    )
    # Should not raise.
    snap = run.main(
        today=today,
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    # No usable history → cold start.
    assert snap.meta.get("cold_start") is True
