"""Daily pipeline orchestrator.

Per BACKEND_BUILD §7 Step 12 with the v0.1.1 amendment — wires all
upstream modules into one run that produces a valid public/data.json.

v0.1.1 changes the trend primitive: n-grams → Claude-extracted named
topics. The flow is now:

    fetch → normalize (now: candidate-hints producer)
          → topics.extract_topics (ONE haiku-4-5 call, the new primitive)
          → score / cluster / Claude enrichment, all keyed on topics.

Day-1 reality: with no snapshot history, sparkline defaults to zero.
Saturation is computed from today's per-source percentiles across the
top-N topics. Convergence fires when a topic appears in ≥3 sources
within 72h (using each source's earliest doc timestamp).

Hard-fail policy: when there are source docs but no Claude call (no
--claude and no injected extract_topics_fn), the run raises rather than
falling back to placeholder data. Placeholder snapshots are how v0.1.0
shipped n-gram noise — that surface is now closed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import anthropic
from dotenv import load_dotenv

from pipeline import cluster as cluster_mod
from pipeline import demand as demand_mod
from pipeline import predict, score, snapshot, summarize, topics
from pipeline.fetch import arxiv, github, hackernews
from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost
from pipeline.models import (
    ConvergenceEvent,
    CreatorAngles,
    DailyBriefing,
    Prediction,
    RiskFlag,
    Snapshot,
    SourceCounts,
    Trend,
)
from pipeline.normalize import extract_candidate_terms
from pipeline.topics import Topic

ROOT = Path(__file__).resolve().parent.parent
TOP_N_TRENDS = 30
DEFAULT_NICHE = "AI tools for solo creators"

ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]
ARXIV_LOOKBACK_DAYS = 2
HN_LOOKBACK_DAYS = 7

PLACEHOLDER_SUMMARY = "(awaiting Claude enrichment)"
PLACEHOLDER_ANGLE = "(awaiting Claude enrichment)"
PLACEHOLDER_BRIEFING_TEXT = "Daily Movers Briefing pending Claude enrichment."

ExtractTopicsFn = Callable[
    [list[Paper], list[HNPost], list[RepoStat], list[str]], list[Topic]
]


# ---------- helpers ----------


def _percentile_ranks(values: list[int]) -> list[float]:
    """Cheap percentile rank: each value's position in the sorted ascending
    order / (n-1) * 100. Ties get the same rank. Zeros stay at 0.
    """
    if not values:
        return []
    sorted_vals = sorted(set(values))
    rank_by_value = {
        v: i / (len(sorted_vals) - 1) * 100 if len(sorted_vals) > 1 else 0.0
        for i, v in enumerate(sorted_vals)
    }
    return [rank_by_value[v] for v in values]


def _build_doc_timestamps(
    papers: list[Paper], posts: list[HNPost], repos: list[RepoStat]
) -> dict[tuple[str, str | int], datetime]:
    """Index source-doc-id → published_at across all three fetchers.

    velocity_from_topic_docs reads this to bucket a topic's attributed
    docs into 7d and 30d windows.
    """
    out: dict[tuple[str, str | int], datetime] = {}
    for p in papers:
        out[("arxiv", p.id)] = p.published_at
    for p in posts:
        out[("hackernews", p.id)] = p.created_at
    for r in repos:
        out[("github", r.full_name)] = r.created_at
    return out


def _source_counts_from_topic(
    topic: Topic,
    doc_timestamps: dict[tuple[str, str | int], datetime],
    today_dt: datetime,
) -> SourceCounts:
    """Bucket a topic's source docs into the SourceCounts shape the data
    contract requires.

    arxiv_30d uses a 30-day window; github_repos_7d and hn_posts_7d use
    7-day windows. The other warming-up fields stay zero — they need
    snapshot-to-snapshot deltas the day-1 pipeline doesn't yet have.
    """
    seven_d_ago = today_dt - timedelta(days=7)
    thirty_d_ago = today_dt - timedelta(days=30)

    def _count(source: str, cutoff: datetime) -> int:
        return sum(
            1
            for doc_id in topic.source_doc_ids.get(source, [])
            if ((ts := doc_timestamps.get((source, doc_id))) is not None and ts >= cutoff)
        )

    return SourceCounts(
        arxiv_30d=_count("arxiv", thirty_d_ago),
        github_repos_7d=_count("github", seven_d_ago),
        hn_posts_7d=_count("hackernews", seven_d_ago),
    )


def _first_appearances_from_topic(
    topic: Topic, doc_timestamps: dict[tuple[str, str | int], datetime]
) -> dict[str, datetime]:
    """Earliest doc timestamp per contributing source, used for convergence."""
    out: dict[str, datetime] = {}
    for src, ids in topic.source_doc_ids.items():
        candidates = [
            doc_timestamps[(src, i)] for i in ids if (src, i) in doc_timestamps
        ]
        if candidates:
            out[src] = min(candidates)
    return out


def _placeholder_creator_angles() -> CreatorAngles:
    return CreatorAngles(
        hook=PLACEHOLDER_ANGLE,
        contrarian=PLACEHOLDER_ANGLE,
        tutorial=PLACEHOLDER_ANGLE,
        eli_creator=PLACEHOLDER_ANGLE,
    )


def _placeholder_risk() -> RiskFlag:
    return RiskFlag(
        breakout_likelihood="medium",
        peak_estimate_days=None,
        risk_flag="single-source signal",
        rationale="(awaiting Claude enrichment)",
    )


def _placeholder_prediction(keyword: str, today: date) -> Prediction:
    return Prediction(
        keyword=keyword,
        text=f"{keyword} placeholder prediction",
        filed_at=today,
        target_date=today + timedelta(days=30),
        verdict="pending",
        lifecycle_at_filing="whisper",
        target_lifecycle="builder",
    )


def _placeholder_briefing() -> DailyBriefing:
    return DailyBriefing(
        text=PLACEHOLDER_BRIEFING_TEXT,
        moved_up=[],
        moved_down=[],
        emerging=[],
        generated_at=datetime.now(tz=timezone.utc),
    )


def _build_trend(
    topic: Topic,
    *,
    today: date,
    sources: SourceCounts,
    saturation_pct: float,
    builder_signal: float,
    cluster_id: int,
    cluster_label: str,
    convergence: ConvergenceEvent,
    velocity_score: float,
) -> Trend:
    velocity_acceleration = 0.0  # warming up — needs day-2+ snapshots
    hidden_gem_score = score.hidden_gem(velocity_score, saturation_pct, builder_signal)
    lifecycle = score.lifecycle_stage(
        arxiv_30d=sources.arxiv_30d,
        github_repos_7d=sources.github_repos_7d,
        hn_points_7d=sources.hn_points_7d,
        saturation=saturation_pct,
        velocity=velocity_score,
        builder_signal=builder_signal,
    )
    tbts_score = score.tbts(
        velocity_score=velocity_score,
        hidden_gem_score=hidden_gem_score,
        lifecycle=lifecycle,
        convergence_detected=convergence.detected,
    )
    return Trend(
        keyword=topic.canonical_name,
        canonical_form=topic.canonical_form,
        cluster_id=cluster_id,
        cluster_label=cluster_label,
        sources=sources,
        velocity_score=velocity_score,
        velocity_acceleration=velocity_acceleration,
        saturation=saturation_pct,
        hidden_gem_score=hidden_gem_score,
        builder_signal=builder_signal,
        lifecycle_stage=lifecycle,
        tbts=tbts_score,
        convergence=convergence,
        summary=PLACEHOLDER_SUMMARY,
        summary_confidence="low",
        angles=_placeholder_creator_angles(),
        risk=_placeholder_risk(),
        prediction=_placeholder_prediction(topic.canonical_name, today),
        sparkline_14d=[],
        aliases=list(topic.aliases),
        source_doc_ids=dict(topic.source_doc_ids),
    )


def _maybe_enrich_with_claude(
    trends: list[Trend], *, niche: str
) -> list[Trend]:
    """Replace placeholder summary/angles/risk on each Trend with live Haiku output."""
    if not trends:
        return trends
    cards = [
        summarize.CardInput(
            keyword=t.keyword,
            cluster_label=t.cluster_label,
            related_terms=list(t.aliases),
            arxiv_papers_7d=t.sources.arxiv_30d,
            github_repos_7d=t.sources.github_repos_7d,
            hn_posts_7d=t.sources.hn_posts_7d,
            velocity_score=t.velocity_score,
            saturation=t.saturation,
            convergence_detected=t.convergence.detected,
            lifecycle_stage=t.lifecycle_stage,
            user_niche=niche,
        )
        for t in trends
    ]
    enriched_by_index = summarize.enrich_cards_batch(cards)
    out: list[Trend] = []
    for i, t in enumerate(trends):
        e = enriched_by_index.get(i)
        if e is None:
            out.append(t)
            continue
        out.append(
            t.model_copy(
                update={
                    "summary": e.summary,
                    "summary_confidence": e.summary_confidence,
                    "angles": e.angles,
                    "risk": e.risk,
                }
            )
        )
    return out


# ---------- entrypoint ----------


def main(
    *,
    today: Optional[date] = None,
    papers: Optional[list[Paper]] = None,
    posts: Optional[list[HNPost]] = None,
    repos: Optional[list[RepoStat]] = None,
    use_claude: bool = False,
    extract_topics_fn: Optional[ExtractTopicsFn] = None,
    public_dir: Path = ROOT / "public",
    predictions_log: Path = ROOT / "data" / "predictions.jsonl",
    niche: str = DEFAULT_NICHE,
) -> Snapshot:
    started = time.time()
    today_d = today or date.today()
    today_dt = datetime.combine(today_d, datetime.min.time()).replace(tzinfo=timezone.utc)

    # ---- 1. Fetch (or accept injected inputs for tests) ----
    fetch_started = time.time()
    fetch_health = {
        "arxiv": True, "hackernews": True, "github": True, "semantic_scholar": False,
    }
    if papers is None:
        try:
            papers = arxiv.fetch_recent_papers(ARXIV_CATEGORIES, ARXIV_LOOKBACK_DAYS)
        except Exception as e:
            print(f"arxiv fetch failed: {e}", file=sys.stderr)
            papers = []
            fetch_health["arxiv"] = False
    if posts is None:
        try:
            posts = hackernews.fetch_ai_posts(HN_LOOKBACK_DAYS)
        except Exception as e:
            print(f"hackernews fetch failed: {e}", file=sys.stderr)
            posts = []
            fetch_health["hackernews"] = False
    if repos is None:
        gh_pat = os.environ.get("GH_PAT", "")
        if gh_pat:
            try:
                repos = github.fetch_trending_repos(gh_pat)
            except Exception as e:
                print(f"github fetch failed: {e}", file=sys.stderr)
                repos = []
                fetch_health["github"] = False
        else:
            repos = []
            fetch_health["github"] = False
    fetch_seconds = round(time.time() - fetch_started, 2)

    # ---- 2. Empty-inputs short-circuit ----
    if not papers and not posts and not repos:
        snap = Snapshot(
            snapshot_date=today_d,
            generated_at=datetime.now(tz=timezone.utc),
            trends=[],
            demand_clusters=[],
            briefing=_placeholder_briefing(),
            hit_rate=predict.compute_hit_rate([]),
            past_predictions=[],
            meta={"empty": True, "fetch_seconds": fetch_seconds},
        )
        snapshot.write_snapshot(snap, public_dir=public_dir)
        return snap

    # ---- 3. Normalize → candidate hints ----
    candidate_terms = extract_candidate_terms(papers, posts, repos)
    candidate_hints = [t.canonical_form for t in candidate_terms]

    # ---- 4. Topic extraction (v0.1.1 — the new primitive) ----
    if extract_topics_fn is not None:
        topic_list = extract_topics_fn(papers, posts, repos, candidate_hints)
    elif use_claude:
        topic_list = topics.extract_topics(papers, posts, repos, candidate_hints)
    else:
        raise RuntimeError(
            "v0.1.1: topic extraction requires --claude (one haiku-4-5 call "
            "per snapshot). Re-run with --claude or pass an extract_topics_fn "
            "in tests. Placeholder n-gram fallback was removed in this version."
        )

    # ---- 5. Trim to TOP_N_TRENDS (Claude already ordered them by signal) ----
    topic_list = topic_list[:TOP_N_TRENDS]

    # ---- 6. Cluster topics into themes ----
    cluster_assignments = cluster_mod.cluster_topics(
        [t.canonical_name for t in topic_list]
    )

    # ---- 7. Per-topic metrics ----
    doc_timestamps = _build_doc_timestamps(papers, posts, repos)

    # Pre-compute SourceCounts and velocity per topic for percentile math.
    counts_per_topic: list[SourceCounts] = []
    velocity_per_topic: list[float] = []
    for topic in topic_list:
        counts = _source_counts_from_topic(topic, doc_timestamps, today_dt)
        counts_per_topic.append(counts)
        _, _, v = score.velocity_from_topic_docs(
            source_doc_ids=topic.source_doc_ids,
            doc_timestamps=doc_timestamps,
            today=today_dt,
        )
        velocity_per_topic.append(v)

    arxiv_pcts = _percentile_ranks([c.arxiv_30d for c in counts_per_topic])
    hn_pcts = _percentile_ranks([c.hn_posts_7d for c in counts_per_topic])
    gh_pcts = _percentile_ranks([c.github_repos_7d for c in counts_per_topic])

    max_github_for_builder_signal = max(
        (c.github_repos_7d for c in counts_per_topic), default=1
    ) or 1

    # ---- 8. Build trends ----
    trends: list[Trend] = []
    for i, topic in enumerate(topic_list):
        sources = counts_per_topic[i]
        builder_sig = sources.github_repos_7d / max_github_for_builder_signal
        saturation_pct = score.saturation(
            github=gh_pcts[i],
            hn=hn_pcts[i],
            arxiv=arxiv_pcts[i],
            semantic_scholar=0.0,
        )
        first_appearances = _first_appearances_from_topic(topic, doc_timestamps)
        convergence = score.detect_convergence(first_appearances)
        ca = cluster_assignments.get(topic.canonical_name)
        cluster_id = ca.cluster_id if ca else -1
        cluster_label = ca.cluster_label if ca else "Unclustered Emerging"
        trends.append(
            _build_trend(
                topic,
                today=today_d,
                sources=sources,
                saturation_pct=saturation_pct,
                builder_signal=builder_sig,
                cluster_id=cluster_id,
                cluster_label=cluster_label,
                convergence=convergence,
                velocity_score=velocity_per_topic[i],
            )
        )

    # ---- 9. Claude enrichment (opt-in) ----
    if use_claude:
        trends = _maybe_enrich_with_claude(trends, niche=niche)

    # ---- 10. Predictions update ----
    preds = predict.load_predictions(predictions_log)
    current_lifecycles = {t.keyword: t.lifecycle_stage for t in trends}
    updated_preds = predict.update_all_verdicts(
        preds, current_lifecycles_by_keyword=current_lifecycles, today=today_d
    )
    hit_rate = predict.compute_hit_rate(updated_preds)
    past_predictions = [p for p in updated_preds if p.verdict != "pending"]

    # ---- 11. Briefing + demand (opt-in for Claude) ----
    if use_claude:
        movers = [
            summarize.TrendMover(
                keyword=t.keyword,
                lifecycle_stage=t.lifecycle_stage,
                velocity_score=t.velocity_score,
                velocity_acceleration=t.velocity_acceleration,
                saturation=t.saturation,
            )
            for t in trends[:10]
        ]
        briefing = summarize.daily_briefing(movers, niche=niche)
        demand_clusters = demand_mod.mine_demand_clusters_for_trends(
            [t.keyword for t in trends[:10]], posts, niche=niche
        )
    else:
        briefing = _placeholder_briefing()
        demand_clusters = []

    # ---- 12. Assemble + write ----
    snap = Snapshot(
        snapshot_date=today_d,
        generated_at=datetime.now(tz=timezone.utc),
        trends=trends,
        demand_clusters=demand_clusters,
        briefing=briefing,
        hit_rate=hit_rate,
        past_predictions=past_predictions[-90:],
        meta={
            "pipeline_runtime_seconds": round(time.time() - started, 2),
            "fetch_seconds": fetch_seconds,
            "sources": {
                "arxiv": {"fetched": len(papers), "ok": fetch_health["arxiv"]},
                "github": {"fetched": len(repos), "ok": fetch_health["github"]},
                "hackernews": {"fetched": len(posts), "ok": fetch_health["hackernews"]},
                "semantic_scholar": {
                    "fetched": 0,
                    "ok": fetch_health["semantic_scholar"],
                },
            },
            "trends_processed": len(trends),
            "use_claude": use_claude,
        },
    )
    snapshot.write_snapshot(snap, public_dir=public_dir)
    return snap


def _cli() -> int:
    parser = argparse.ArgumentParser(description="AI Alpha Radar daily pipeline")
    parser.add_argument(
        "--claude",
        action="store_true",
        help="Enable live Claude calls (topic extraction is required as of v0.1.1)",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env.local", override=True)
    snap = main(use_claude=args.claude)
    print(
        f"snapshot written: {len(snap.trends)} trends, "
        f"{len(snap.demand_clusters)} demand clusters, "
        f"{len(snap.past_predictions)} past predictions; "
        f"runtime {snap.meta['pipeline_runtime_seconds']}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
