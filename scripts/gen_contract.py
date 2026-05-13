"""Generate docs/DATA_CONTRACT.md from an example Snapshot.

Run with: poetry run python scripts/gen_contract.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import models  # noqa: E402  (path shim runs first)


def build_example_snapshot() -> models.Snapshot:
    trend = models.Trend(
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
    demand = models.DemandCluster(
        question_shape="How do I run MCP servers locally with Claude Desktop?",
        askers_estimate=34,
        quotes=[
            models.DemandQuote(
                text="Has anyone got Claude Desktop talking to a local MCP server reliably?",
                source="HN - Show HN: MCP server template",
            ),
            models.DemandQuote(
                text="The docs assume stdio transport but my server only does sse - help",
                source="HN - Ask HN comments",
            ),
        ],
        sources=["hackernews"],
        weekly_growth_pct=18.7,
        open_window_days=14,
        creator_brief=(
            "A short tutorial that walks through stdio vs sse and shows one working "
            "Claude Desktop config closes the gap. Two minutes is enough."
        ),
        related_trends=["world-model-agents"],
    )
    briefing = models.DailyBriefing(
        text=(
            "What moved: World Model Agents (+1.2 acceleration). What died: prompt-engineering "
            "newsletters. What's emerging: local MCP servers."
        ),
        moved_up=["world-model-agents"],
        moved_down=["prompt-engineering"],
        emerging=["mcp-tools"],
        generated_at=datetime(2026, 5, 13, 6, 14, 22, tzinfo=timezone.utc),
    )
    hit_rate = models.HitRate(rate=0.62, verified=10, tracking=4, verified_early=2, wrong=4)
    return models.Snapshot(
        snapshot_date=date(2026, 5, 13),
        generated_at=datetime(2026, 5, 13, 6, 14, 22, tzinfo=timezone.utc),
        trends=[trend],
        demand_clusters=[demand],
        briefing=briefing,
        hit_rate=hit_rate,
        past_predictions=[trend.prediction],
        meta={
            "pipeline_runtime_seconds": 142,
            "sources": {
                "arxiv": {"fetched": 187, "ok": True},
                "github": {"fetched": 42, "ok": True},
                "hackernews": {"fetched": 64, "ok": True},
                "semantic_scholar": {"fetched": 180, "ok": True},
            },
            "claude_cost_usd": 0.043,
            "claude_cache_hit_rate": 0.78,
        },
    )


def main() -> None:
    snap = build_example_snapshot()
    payload = json.loads(snap.model_dump_json())
    pretty = json.dumps(payload, indent=2, sort_keys=False)

    # Self-check: the embedded JSON must round-trip.
    models.Snapshot.model_validate_json(pretty)

    out = ROOT / "docs" / "DATA_CONTRACT.md"
    out.write_text(
        "# Data Contract - `public/data.json`\n\n"
        "Auto-generated by `scripts/gen_contract.py`. The shape below is the\n"
        "frontend interface; any change requires a frontend coordination note.\n\n"
        "```json\n"
        f"{pretty}\n"
        "```\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
