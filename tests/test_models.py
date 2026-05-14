"""TDD for pipeline.models — the data contract is the frontend interface."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from pipeline import models


def _example_trend() -> models.Trend:
    return models.Trend(
        keyword="World Model Agents",
        canonical_form="world-model-agents",
        cluster_id=3,
        cluster_label="Autonomous Reasoning",
        sources=models.SourceCounts(
            arxiv_30d=42,
            github_repos_7d=8,
            github_stars_7d=240,
            hn_posts_7d=11,
            hn_points_7d=560,
            semantic_scholar_citations_7d=15,
        ),
        velocity_score=3.8,
        velocity_acceleration=1.2,
        saturation=27.5,
        hidden_gem_score=0.74,
        builder_signal=0.62,
        lifecycle_stage="builder",
        tbts=78,
        convergence=models.ConvergenceEvent(
            detected=True,
            sources_hit=["arxiv", "hackernews", "github"],
            window_hours=48,
            first_appearance={
                "arxiv": datetime(2026, 5, 10, 4, 0, tzinfo=timezone.utc),
                "hackernews": datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc),
                "github": datetime(2026, 5, 12, 9, 15, tzinfo=timezone.utc),
            },
        ),
        summary="Agents that learn an internal world model are showing up everywhere.",
        summary_confidence="high",
        angles=models.CreatorAngles(
            hook="The 'thinking AI' you'll see everywhere in 6 weeks",
            contrarian="World models are overhyped - here's why",
            tutorial="Build a tiny world-model agent in 10 minutes",
            eli_creator="Like a chess AI that imagines its next 10 moves before speaking.",
        ),
        risk=models.RiskFlag(
            breakout_likelihood="high",
            peak_estimate_days=21,
            risk_flag="single-source signal",
            rationale="Strong arxiv velocity but GitHub still nascent.",
        ),
        prediction=models.Prediction(
            text="World-model agents reach saturation > 50 by 2026-06-15",
            filed_at=date(2026, 5, 13),
            target_date=date(2026, 6, 15),
            verdict="tracking",
        ),
        sparkline_14d=[1, 2, 2, 3, 3, 4, 4, 5, 6, 7, 7, 8, 9, 10],
    )


def test_trend_roundtrip() -> None:
    trend = _example_trend()
    parsed = models.Trend.model_validate_json(trend.model_dump_json())
    assert parsed == trend


def test_source_counts_defaults_zero() -> None:
    counts = models.SourceCounts()
    assert counts.arxiv_30d == 0
    assert counts.github_repos_7d == 0
    assert counts.youtube_videos_7d == 0
    assert counts.x_posts_7d == 0


def test_lifecycle_stage_rejects_unknown_value() -> None:
    bad = _example_trend().model_dump()
    bad["lifecycle_stage"] = "moon"
    with pytest.raises(ValidationError):
        models.Trend.model_validate(bad)


def test_source_name_literal_rejects_unknown_in_convergence() -> None:
    with pytest.raises(ValidationError):
        models.ConvergenceEvent(
            detected=False,
            sources_hit=["medium_dot_com"],
            window_hours=0,
            first_appearance={},
        )


def test_demand_quote_url_optional() -> None:
    cluster = models.DemandCluster(
        question_shape="How do I run X on Y?",
        askers_estimate=12,
        quotes=[models.DemandQuote(text="Anyone got X working on Y?", source="HN")],
        sources=["hackernews"],
        weekly_growth_pct=14.3,
        open_window_days=21,
        creator_brief="A short how-to fills the void.",
        related_trends=["world-model-agents"],
    )
    assert cluster.quotes[0].raw_url is None


def test_prediction_verdict_fields_default_none() -> None:
    pred = models.Prediction(
        text="X happens",
        filed_at=date(2026, 5, 1),
        target_date=date(2026, 5, 30),
        verdict="pending",
    )
    assert pred.verdict_text is None
    assert pred.verified_at is None


def test_risk_flag_allows_null_peak_estimate() -> None:
    risk = models.RiskFlag(
        breakout_likelihood="low",
        peak_estimate_days=None,
        risk_flag="none",
        rationale="Too early to estimate.",
    )
    assert risk.peak_estimate_days is None


def test_snapshot_default_data_freshness_status_is_live() -> None:
    snap = models.Snapshot(
        snapshot_date=date(2026, 5, 13),
        generated_at=datetime(2026, 5, 13, 6, 0, tzinfo=timezone.utc),
        trends=[],
        demand_clusters=[],
        briefing=models.DailyBriefing(
            text="",
            moved_up=[],
            moved_down=[],
            emerging=[],
            generated_at=datetime(2026, 5, 13, 6, 0, tzinfo=timezone.utc),
        ),
        hit_rate=models.HitRate(rate=0.0, verified=0, tracking=0, verified_early=0, wrong=0),
        past_predictions=[],
        meta={},
    )
    assert snap.data_freshness_status == "live"
    # Round-trips with new field.
    parsed = models.Snapshot.model_validate_json(snap.model_dump_json())
    assert parsed.data_freshness_status == "live"


def test_snapshot_data_freshness_status_accepts_stale_and_error() -> None:
    base = dict(
        snapshot_date=date(2026, 5, 13),
        generated_at=datetime(2026, 5, 13, 6, 0, tzinfo=timezone.utc),
        trends=[],
        demand_clusters=[],
        briefing=models.DailyBriefing(
            text="",
            moved_up=[],
            moved_down=[],
            emerging=[],
            generated_at=datetime(2026, 5, 13, 6, 0, tzinfo=timezone.utc),
        ),
        hit_rate=models.HitRate(rate=0.0, verified=0, tracking=0, verified_early=0, wrong=0),
        past_predictions=[],
        meta={},
    )
    for status in ("live", "stale", "error"):
        snap = models.Snapshot(data_freshness_status=status, **base)
        assert snap.data_freshness_status == status
    with pytest.raises(ValidationError):
        models.Snapshot(data_freshness_status="bogus", **base)


def test_snapshot_roundtrip() -> None:
    trend = _example_trend()
    snapshot = models.Snapshot(
        snapshot_date=date(2026, 5, 13),
        generated_at=datetime(2026, 5, 13, 6, 14, 22, tzinfo=timezone.utc),
        trends=[trend],
        demand_clusters=[
            models.DemandCluster(
                question_shape="How do I run MCP servers locally?",
                askers_estimate=34,
                quotes=[models.DemandQuote(text="Docs assume stdio", source="HN")],
                sources=["hackernews"],
                weekly_growth_pct=18.7,
                open_window_days=14,
                creator_brief="Walk through stdio vs sse.",
                related_trends=["world-model-agents"],
            )
        ],
        briefing=models.DailyBriefing(
            text="Today's movers: world-model-agents.",
            moved_up=["world-model-agents"],
            moved_down=[],
            emerging=["mcp-tools"],
            generated_at=datetime(2026, 5, 13, 6, 14, 22, tzinfo=timezone.utc),
        ),
        hit_rate=models.HitRate(rate=0.62, verified=10, tracking=4, verified_early=2, wrong=4),
        past_predictions=[trend.prediction],
        meta={"pipeline_runtime_seconds": 142, "claude_cost_usd": 0.043},
    )
    parsed = models.Snapshot.model_validate_json(snapshot.model_dump_json())
    assert parsed == snapshot
