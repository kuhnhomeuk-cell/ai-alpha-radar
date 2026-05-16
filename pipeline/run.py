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

from pipeline import burst, calibration, changepoint, cluster as cluster_mod, cluster_identity
from pipeline import demand as demand_mod
from pipeline import leadlag, meta_trends
from pipeline import novelty as novelty_mod
from pipeline import predict, questions as question_mining, rrf, score, snapshot, summarize, topics
from pipeline.fetch import arxiv, github, hackernews
from pipeline.fetch import bluesky, huggingface, newsletters
from pipeline.fetch import perplexity as perplexity_fetcher
from pipeline.fetch import producthunt as producthunt_fetcher
from pipeline.fetch import reddit as reddit_fetcher
from pipeline.fetch import replicate as replicate_fetcher
from pipeline.fetch import semantic_scholar
from pipeline.fetch import youtube_outliers as youtube_outliers_fetcher
from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost
from pipeline.fetch.huggingface import HFModel
from pipeline.fetch.newsletters import NewsletterSignal as FetchedNewsletterSignal
from pipeline.fetch.producthunt import ProductHuntLaunch
from pipeline.fetch.reddit import RedditPost
from pipeline.fetch.replicate import ReplicateModel
from pipeline.log import log
from pipeline.models import (
    ConvergenceEvent,
    CreatorAngles,
    DailyBriefing,
    NewsletterSignal,
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


def _topic_match_strings_for_form(t: Trend) -> list[str]:
    """Same idea as _topic_match_strings but reads from a fully-built Trend."""
    out = {t.canonical_form.lower(), t.keyword.lower()}
    for a in t.aliases:
        if a:
            out.add(a.lower())
    return [s for s in out if len(s) >= 3]


def _topic_match_strings(topic: Topic) -> list[str]:
    """Lowercased substrings used when scanning external source text for a topic.

    The Claude topic extractor returns a canonical_name, canonical_form
    (hyphenated), and 0-3 aliases. The aliases are usually the highest-
    recall match against raw source text (acronyms, alternate phrasings).
    """
    out = {topic.canonical_form.lower(), topic.canonical_name.lower()}
    for a in topic.aliases:
        if a:
            out.add(a.lower())
    return [s for s in out if len(s) >= 3]


def _huggingface_per_topic(
    hf_models: list[HFModel], topic_list: list[Topic]
) -> dict[str, dict[str, int]]:
    """Sum likes/downloads across HF models whose name or tags match a topic."""
    if not hf_models:
        return {}
    model_texts = [(m, huggingface.model_text(m).lower()) for m in hf_models]
    out: dict[str, dict[str, int]] = {}
    for topic in topic_list:
        needles = _topic_match_strings(topic)
        likes = 0
        downloads = 0
        for m, text in model_texts:
            if any(n in text for n in needles):
                likes += m.likes
                downloads += m.downloads
        if likes or downloads:
            out[topic.canonical_form] = {"likes": likes, "downloads": downloads}
    return out


def _reddit_per_topic(
    reddit_posts: list[RedditPost], topic_list: list[Topic]
) -> dict[str, dict[str, object]]:
    """Per-topic Reddit post mentions + top subreddit."""
    if not reddit_posts:
        return {}
    post_text = [
        (p, (p.title + " " + (p.selftext or "")).lower(), p.subreddit) for p in reddit_posts
    ]
    out: dict[str, dict[str, object]] = {}
    for topic in topic_list:
        needles = _topic_match_strings(topic)
        sub_counts: dict[str, int] = {}
        total = 0
        for _, text, sub in post_text:
            if any(n in text for n in needles):
                total += 1
                sub_counts[sub] = sub_counts.get(sub, 0) + 1
        if total:
            top_sub = max(sub_counts.items(), key=lambda kv: kv[1])[0]
            out[topic.canonical_form] = {"count": total, "top_subreddit": top_sub}
    return out


def _producthunt_per_topic(
    launches: list[ProductHuntLaunch], topic_list: list[Topic]
) -> dict[str, int]:
    if not launches:
        return {}
    launch_text = [(l.name + " " + (l.tagline or "")).lower() for l in launches]
    out: dict[str, int] = {}
    for topic in topic_list:
        needles = _topic_match_strings(topic)
        total = sum(1 for t in launch_text if any(n in t for n in needles))
        if total:
            out[topic.canonical_form] = total
    return out


def _replicate_per_topic(
    models: list[ReplicateModel], topic_list: list[Topic]
) -> dict[str, int]:
    if not models:
        return {}
    model_text = [(m.name + " " + (m.description or "")).lower() for m in models]
    out: dict[str, int] = {}
    for topic in topic_list:
        needles = _topic_match_strings(topic)
        runs = sum(m.run_count for m, t in zip(models, model_text) if any(n in t for n in needles))
        if runs:
            out[topic.canonical_form] = runs
    return out


def _s2_per_topic(
    papers: list[Paper], topic_list: list[Topic], s2_data: dict
) -> dict[str, int]:
    if not s2_data:
        return {}
    paper_text = [(p.id, (p.title + " " + p.abstract).lower()) for p in papers]
    out: dict[str, int] = {}
    for topic in topic_list:
        needles = _topic_match_strings(topic)
        total = 0
        seen_ids: set[str] = set()
        for pid, text in paper_text:
            if pid in seen_ids or pid not in s2_data:
                continue
            if any(n in text for n in needles):
                info = s2_data[pid]
                total += getattr(info, "citation_count", 0) if hasattr(info, "citation_count") else (info.get("citation_count", 0) if isinstance(info, dict) else 0)
                seen_ids.add(pid)
        if total:
            out[topic.canonical_form] = total
    return out


def _source_counts_from_topic(
    topic: Topic,
    doc_timestamps: dict[tuple[str, str | int], datetime],
    today_dt: datetime,
    *,
    hf_count: Optional[dict[str, int]] = None,
    reddit_count: int = 0,
    producthunt_count: int = 0,
    replicate_delta: int = 0,
    bluesky_count: int = 0,
    s2_citations: int = 0,
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
        semantic_scholar_citations_7d=s2_citations,
        huggingface_likes_7d=(hf_count or {}).get("likes", 0),
        huggingface_downloads_7d=(hf_count or {}).get("downloads", 0),
        bluesky_mentions_7d=bluesky_count,
        reddit_mentions_7d=reddit_count,
        producthunt_launches_7d=producthunt_count,
        replicate_runs_7d_delta=replicate_delta,
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


CONSENSUS_SOURCES = (
    "arxiv", "github", "hackernews", "reddit", "huggingface",
    "producthunt", "replicate", "bluesky", "semantic_scholar",
)


def _sources_confirming_for_topic(
    sources: SourceCounts, active: list[str]
) -> list[str]:
    """Inspect this topic's SourceCounts to determine which of the active
    sources contributed at least one attributed signal. Source-specific
    field-existence check; order matches CONSENSUS_SOURCES.
    """
    has_signal = {
        "arxiv": sources.arxiv_30d > 0,
        "github": sources.github_repos_7d > 0,
        "hackernews": sources.hn_posts_7d > 0,
        "reddit": sources.reddit_mentions_7d > 0,
        "huggingface": (sources.huggingface_likes_7d + sources.huggingface_downloads_7d) > 0,
        "producthunt": sources.producthunt_launches_7d > 0,
        "replicate": sources.replicate_runs_7d_delta > 0,
        "bluesky": sources.bluesky_mentions_7d > 0,
        "semantic_scholar": sources.semantic_scholar_citations_7d > 0,
    }
    return [src for src in active if has_signal.get(src, False)]


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
    reddit_top_subreddit: Optional[str] = None,
    velocity_acceleration: float = 0.0,
    velocity_significance: float = 0.0,
    burst_score_val: float = 0.0,
    novelty_score_val: float = 0.0,
    sparkline: Optional[list[int]] = None,
    papers_by_id: Optional[dict[str, Paper]] = None,
    active_consensus_sources: Optional[list[str]] = None,
) -> Trend:
    hidden_gem_score = score.hidden_gem(velocity_score, saturation_pct, builder_signal)
    # v0.2.0 — average venue-boost across this topic's attributed arXiv
    # papers and add 0.2 * avg to hidden_gem_score (capped at 1.0). Papers
    # accepted at ICML/NeurIPS/ICLR/etc. surface as hidden gems faster.
    if papers_by_id:
        arxiv_ids = topic.source_doc_ids.get("arxiv", [])
        boosts = [
            score.venue_boost(papers_by_id[doc_id].comment)
            for doc_id in arxiv_ids
            if isinstance(doc_id, str) and doc_id in papers_by_id
        ]
        if boosts:
            hidden_gem_score = min(hidden_gem_score + 0.2 * (sum(boosts) / len(boosts)), 1.0)

    # v0.2.0 — cross-source consensus from the SourceCounts shape.
    active = active_consensus_sources or list(CONSENSUS_SOURCES)
    sources_confirming = _sources_confirming_for_topic(sources, active)
    consensus_ratio = score.cross_source_consensus(sources_confirming, len(active))
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
        reddit_top_subreddit=reddit_top_subreddit,
        sources=sources,
        velocity_score=velocity_score,
        velocity_acceleration=velocity_acceleration,
        velocity_significance=velocity_significance,
        burst_score=burst_score_val,
        novelty_score=novelty_score_val,
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
        sparkline_14d=sparkline or [],
        aliases=list(topic.aliases),
        source_doc_ids=dict(topic.source_doc_ids),
        sources_confirming=sources_confirming,
        consensus_ratio=consensus_ratio,
    )


def _maybe_enrich_with_perplexity(
    trends: list[Trend], *, budget_cents: Optional[float] = None
) -> tuple[list[Trend], float]:
    """Wave 5 — replace empty `pain_points` with Sonar pain-points per trend.

    Stops early when `budget_cents` is exhausted (remaining trends keep their
    empty `pain_points`). Individual failures degrade silently — pain-points
    are enrichment, not a hard input. Returns (enriched_trends, total_cents).
    """
    if not trends:
        return trends, 0.0
    spent_cents: float = 0.0
    out: list[Trend] = []
    for t in trends:
        if budget_cents is not None and spent_cents >= budget_cents:
            out.append(t)
            continue
        points, cost = perplexity_fetcher.fetch_pain_points(t.keyword)
        spent_cents += cost
        if points:
            out.append(t.model_copy(update={"pain_points": points}))
        else:
            out.append(t)
    return out, spent_cents


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


MIN_OK_SOURCES = 3  # Audit 1.5 — refuse to ship a snapshot built on fewer.
VELOCITY_LOOKBACK_DAYS = 30  # how many prior snapshots feed velocity/burst
SPARKLINE_DAYS = 14


def _load_history(
    public_dir: Path, today: date, days: int
) -> dict[date, Snapshot]:
    """Load up to `days` prior snapshots before today, dropping bad files."""
    history: dict[date, Snapshot] = {}
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        snap = snapshot.read_prior_snapshot(d, public_dir=public_dir)
        if snap is not None:
            history[d] = snap
    return history


def _topic_daily_total_series(
    history: dict[date, Snapshot],
    canonical_form: str,
    today: date,
    days: int,
) -> list[int]:
    """Daily series of (arxiv_30d + github_repos_7d + hn_posts_7d) for one topic.

    Used to feed burst, changepoint, mann-kendall, and the sparkline. Missing
    days slot in as zero so the series length always equals `days`.
    """
    out: list[int] = []
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        snap = history.get(d)
        total = 0
        if snap is not None:
            for t in snap.trends:
                if t.canonical_form == canonical_form:
                    total = (
                        t.sources.arxiv_30d
                        + t.sources.github_repos_7d
                        + t.sources.hn_posts_7d
                    )
                    break
        out.append(total)
    return out


def main(
    *,
    today: Optional[date] = None,
    papers: Optional[list[Paper]] = None,
    posts: Optional[list[HNPost]] = None,
    repos: Optional[list[RepoStat]] = None,
    hf_models: Optional[list[HFModel]] = None,
    newsletter_signals: Optional[list[FetchedNewsletterSignal]] = None,
    reddit_posts: Optional[list[RedditPost]] = None,
    producthunt_launches: Optional[list[ProductHuntLaunch]] = None,
    replicate_models: Optional[list[ReplicateModel]] = None,
    s2_data: Optional[dict] = None,
    bluesky_db_path: Optional[Path] = None,
    use_claude: bool = False,
    extract_topics_fn: Optional[ExtractTopicsFn] = None,
    public_dir: Path = ROOT / "public",
    predictions_log: Path = ROOT / "data" / "predictions.jsonl",
    niche: str = DEFAULT_NICHE,
    max_cost_cents: Optional[float] = None,
) -> Snapshot:
    started = time.time()
    today_d = today or date.today()
    today_dt = datetime.combine(today_d, datetime.min.time()).replace(tzinfo=timezone.utc)

    # ---- 1. Fetch (or accept injected inputs for tests) ----
    fetch_started = time.time()
    fetch_health = {
        "arxiv": True,
        "hackernews": True,
        "github": True,
        "semantic_scholar": False,
        "huggingface": False,
        "reddit": False,
        "producthunt": False,
        "replicate": False,
    }
    if papers is None:
        try:
            papers = arxiv.fetch_recent_papers(ARXIV_CATEGORIES, ARXIV_LOOKBACK_DAYS)
        except Exception as e:
            log("fetch_failed", level="warning", source="arxiv", error=str(e))
            papers = []
            fetch_health["arxiv"] = False
    if posts is None:
        try:
            # v0.2.0 — enable points-floored keyword sweep + 3 tag-only passes
            # (Show HN / front_page / Ask HN). Tests that pin call counts call
            # fetch_ai_posts with explicit keyword/extra_passes args.
            posts = hackernews.fetch_ai_posts(
                HN_LOOKBACK_DAYS,
                min_points=hackernews.HN_MIN_POINTS_KEYWORD,
                extra_passes=hackernews.EXTRA_PASS_NAMES,
            )
        except Exception as e:
            log("fetch_failed", level="warning", source="hackernews", error=str(e))
            posts = []
            fetch_health["hackernews"] = False
    if repos is None:
        gh_pat = os.environ.get("GH_PAT", "")
        if gh_pat:
            try:
                repos = github.fetch_trending_repos(gh_pat)
            except Exception as e:
                log("fetch_failed", level="warning", source="github", error=str(e))
                repos = []
                fetch_health["github"] = False
        else:
            repos = []
            fetch_health["github"] = False

    # Hugging Face Hub trending — no auth, free public endpoint.
    if hf_models is None:
        try:
            # v0.2.0 — pass snapshots_dir so download velocity attaches
            # against the 7-day-prior snapshot. Day-1 (no prior) is fine:
            # warming_up stays True, downloads_7d_delta stays None.
            hf_models = huggingface.fetch_trending_models(
                snapshots_dir=public_dir / "snapshots",
            )
        except Exception as e:
            log("fetch_failed", level="warning", source="huggingface", error=str(e))
            hf_models = []
    fetch_health["huggingface"] = len(hf_models) > 0

    # Newsletter RSS cross-mentions — no auth.
    if newsletter_signals is None:
        try:
            newsletter_signals = newsletters.fetch_newsletter_signals(today=today_dt)
        except Exception as e:
            log("fetch_failed", level="warning", source="newsletters", error=str(e))
            newsletter_signals = []

    # Reddit — needs creds; empty list on miss.
    if reddit_posts is None:
        try:
            reddit_posts = reddit_fetcher.fetch_top_posts()
        except Exception as e:
            log("fetch_failed", level="warning", source="reddit", error=str(e))
            reddit_posts = []
    fetch_health["reddit"] = len(reddit_posts) > 0

    # Product Hunt — needs PRODUCT_HUNT_TOKEN.
    if producthunt_launches is None:
        try:
            producthunt_launches = producthunt_fetcher.fetch_trending_launches()
        except Exception as e:
            log("fetch_failed", level="warning", source="producthunt", error=str(e))
            producthunt_launches = []
    fetch_health["producthunt"] = len(producthunt_launches) > 0

    # Replicate — needs REPLICATE_API_KEY.
    if replicate_models is None:
        try:
            replicate_models = replicate_fetcher.fetch_trending()
        except Exception as e:
            log("fetch_failed", level="warning", source="replicate", error=str(e))
            replicate_models = []
    fetch_health["replicate"] = len(replicate_models) > 0

    # Semantic Scholar enrichment on the arxiv ids we just fetched.
    if s2_data is None:
        if papers:
            try:
                s2_data = semantic_scholar.enrich_papers(
                    [p.id for p in papers],
                    api_key=os.environ.get("SEMANTIC_SCHOLAR_KEY") or None,
                )
            except Exception as e:
                log("fetch_failed", level="warning", source="semantic_scholar", error=str(e))
                s2_data = {}
        else:
            s2_data = {}
    fetch_health["semantic_scholar"] = bool(s2_data)

    # Wave 5 — YouTube outliers, read from the operator-refreshed disk cache.
    # No network call; safe to run unconditionally. Missing/malformed file
    # degrades to []. Cache is regenerated via scripts/refresh_youtube_outliers.py.
    youtube_outliers_payload = youtube_outliers_fetcher.fetch_youtube_outliers()

    fetch_seconds = round(time.time() - fetch_started, 2)

    # Truthfulness gate — Audit 1.5. Refuse to ship if fewer than 3 sources ok.
    ok_sources = sum(1 for h in fetch_health.values() if h)
    if ok_sources < MIN_OK_SOURCES and not (papers or posts or repos):
        log(
            "fetch_health_below_floor",
            level="error",
            ok_sources=ok_sources,
            total_sources=len(fetch_health),
            min_ok_sources=MIN_OK_SOURCES,
            failed=sorted(k for k, v in fetch_health.items() if not v),
        )
        sys.exit(2)

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
    # Audit 3.1, 3.3, 3.4, 3.5 — fold names from the Wave 3a fetchers into
    # the hint pool so the topic extractor sees the same surface area as
    # the per-topic aggregators downstream.
    for m in hf_models:
        candidate_hints.append(huggingface.model_name(m))
    for p in reddit_posts:
        candidate_hints.append(p.title[:80])
    for l in producthunt_launches:
        candidate_hints.append(l.name)
    for m in replicate_models:
        candidate_hints.append(m.name)
    candidate_hints = [h for h in candidate_hints if h]

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

    # Load history early — cluster_identity needs yesterday's centroids
    # before the cluster step runs.
    history = _load_history(public_dir, today_d, VELOCITY_LOOKBACK_DAYS)

    # ---- 6. Cluster topics into themes + stabilize IDs against yesterday ----
    cluster_assignments, raw_centroids = cluster_mod.cluster_topics_with_centroids(
        [t.canonical_name for t in topic_list]
    )

    # Audit 2.6 — map this run's HDBSCAN-assigned cluster ids back onto
    # yesterday's stable ids when centroids are within threshold.
    import numpy as np

    new_centroids_np = {cid: np.asarray(v) for cid, v in raw_centroids.items()}
    prior_snapshot_for_clusters = history.get(today_d - timedelta(days=1)) if history else None
    prior_centroids_np: dict[int, "np.ndarray"] = {}
    if prior_snapshot_for_clusters is not None:
        prior_centroids_np = {
            cid: np.asarray(vec)
            for cid, vec in prior_snapshot_for_clusters.cluster_centroids.items()
        }
    labels_by_new_id: dict[int, str] = {}
    for name, ca in cluster_assignments.items():
        labels_by_new_id.setdefault(ca.cluster_id, ca.cluster_label)
    id_remap = cluster_identity.canonicalize_cluster_ids(
        new_centroids_np,
        prior_centroids_np,
        labels_by_new_id=labels_by_new_id,
    )
    # Apply the remap to each assignment + the centroids that ship in the
    # snapshot.
    for name, ca in cluster_assignments.items():
        cluster_assignments[name] = cluster_mod.ClusterAssignment(
            cluster_id=id_remap.get(ca.cluster_id, ca.cluster_id),
            cluster_label=ca.cluster_label,
        )
    canonical_centroids: dict[int, list[float]] = {
        id_remap.get(cid, cid): vec for cid, vec in raw_centroids.items()
    }

    # ---- 7. Per-topic metrics ----
    doc_timestamps = _build_doc_timestamps(papers, posts, repos)
    # v0.2.0 — index by arxiv id so _build_trend can compute venue_boost from
    # each topic's attributed papers' arxiv:comment fields.
    papers_by_id: dict[str, Paper] = {p.id: p for p in papers}

    # External-source aggregations per topic (new in this phase).
    hf_per_topic = _huggingface_per_topic(hf_models, topic_list)
    reddit_per_topic = _reddit_per_topic(reddit_posts, topic_list)
    producthunt_per_topic = _producthunt_per_topic(producthunt_launches, topic_list)
    replicate_per_topic = _replicate_per_topic(replicate_models, topic_list)
    s2_per_topic = _s2_per_topic(papers, topic_list, s2_data)

    # Bluesky counts use the canonical_form as the keyword and a 7-day window.
    bsky_path = bluesky_db_path if bluesky_db_path is not None else bluesky.DEFAULT_DB_PATH
    try:
        bluesky_counts = bluesky.read_mention_counts(
            bsky_path,
            keywords={t.canonical_form for t in topic_list},
            since=today_dt - timedelta(days=7),
        )
    except Exception as e:
        log("fetch_failed", level="warning", source="bluesky", error=str(e))
        bluesky_counts = {}

    # v0.2.0 — active sources for cross-source consensus: only sources that
    # fetched at least one doc this run get to vote. Drops to single-digit
    # active when fetchers are stale, which the consensus_ratio then reflects.
    active_consensus_sources: list[str] = [
        s for s in CONSENSUS_SOURCES
        if (
            (s == "arxiv" and papers)
            or (s == "github" and repos)
            or (s == "hackernews" and posts)
            or (s == "reddit" and reddit_posts)
            or (s == "huggingface" and hf_models)
            or (s == "producthunt" and producthunt_launches)
            or (s == "replicate" and replicate_models)
            or (s == "bluesky" and bluesky_counts)
            or (s == "semantic_scholar" and s2_data)
        )
    ]

    # Pre-compute SourceCounts and velocity per topic for percentile math.
    counts_per_topic: list[SourceCounts] = []
    velocity_per_topic: list[float] = []
    for topic in topic_list:
        reddit_info = reddit_per_topic.get(topic.canonical_form, {})
        counts = _source_counts_from_topic(
            topic,
            doc_timestamps,
            today_dt,
            hf_count=hf_per_topic.get(topic.canonical_form),
            reddit_count=int(reddit_info.get("count", 0)) if reddit_info else 0,
            producthunt_count=producthunt_per_topic.get(topic.canonical_form, 0),
            replicate_delta=replicate_per_topic.get(topic.canonical_form, 0),
            bluesky_count=bluesky_counts.get(topic.canonical_form, 0),
            s2_citations=s2_per_topic.get(topic.canonical_form, 0),
        )
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

    # Novelty: build today's centroid from topic canonical_names and compare
    # to the persisted 60d rolling corpus centroid. Day-1 with no centroid
    # yet returns 0.0 distance and seeds the file for tomorrow.
    novelty_scores = novelty_mod.score_topics_against_corpus(
        topic_canonical_names=[t.canonical_name for t in topic_list],
        corpus_centroid_path=ROOT / "data" / "corpus_centroid_60d.npy",
    )

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
        reddit_info = reddit_per_topic.get(topic.canonical_form, {})

        # Audit 1.1, 2.2, 2.3, 2.9, 3.11 — history-derived per-topic series.
        daily_series_30d = _topic_daily_total_series(
            history, topic.canonical_form, today_d, VELOCITY_LOOKBACK_DAYS
        )
        today_total = (
            sources.arxiv_30d + sources.github_repos_7d + sources.hn_posts_7d
        )
        # Sparkline trims to last 14 days then appends today.
        sparkline = daily_series_30d[-SPARKLINE_DAYS + 1 :] + [today_total]
        # Burst on the 30d series + today.
        full_series = daily_series_30d + [today_total]
        burst_val = burst.burst_score(full_series)
        # Changepoint-derived acceleration vs. last breakpoint.
        accel = changepoint.velocity_acceleration(
            daily_series_30d, today_count=today_total
        )
        # Mann-Kendall significance (z-score) on the same series.
        mk_z = score.mann_kendall_confidence(full_series)

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
                reddit_top_subreddit=reddit_info.get("top_subreddit") if reddit_info else None,
                velocity_acceleration=accel,
                velocity_significance=mk_z,
                burst_score_val=burst_val,
                novelty_score_val=novelty_scores.get(topic.canonical_name, 0.0),
                sparkline=sparkline,
                papers_by_id=papers_by_id,
                active_consensus_sources=active_consensus_sources,
            )
        )

    # ---- 8a. Meta-trends (audit 3.13) — HDBSCAN over canonical centroids ----
    cluster_to_meta = meta_trends.cluster_centroids(canonical_centroids)
    cluster_labels_for_meta: dict[int, str] = {}
    for t in trends:
        cluster_labels_for_meta.setdefault(t.cluster_id, t.cluster_label)
    meta_labels = meta_trends.build_meta_trend_labels(
        cluster_to_meta, cluster_labels_for_meta
    )
    cluster_to_meta_label = {
        cid: meta_labels.get(meta_id) for cid, meta_id in cluster_to_meta.items()
    }
    for t in trends:
        label = cluster_to_meta_label.get(t.cluster_id)
        if label:
            t.meta_trend = label

    # ---- 8b. RRF (audit 3.7) — fuse rankings across burst/novelty/velocity ----
    rankings = {
        "velocity": rrf.ranks_from_counts(
            {t.canonical_form: int(t.velocity_score * 100) for t in trends}
        ),
        "burst": rrf.ranks_from_counts(
            {t.canonical_form: int(t.burst_score * 100) for t in trends}
        ),
        "novelty": rrf.ranks_from_counts(
            {t.canonical_form: int(t.novelty_score * 100) for t in trends}
        ),
        "hidden_gem": rrf.ranks_from_counts(
            {t.canonical_form: int(t.hidden_gem_score * 100) for t in trends}
        ),
    }
    fused = rrf.rrf_score(rankings)
    for t in trends:
        t.rrf_score = fused.get(t.canonical_form, 0.0)

    # ---- 8c. Lead-lag still_early_gate (audit 3.8) ----
    for t in trends:
        arxiv_series = []
        hn_series = []
        for i_d in range(VELOCITY_LOOKBACK_DAYS, 0, -1):
            d = today_d - timedelta(days=i_d)
            snap_d = history.get(d)
            if snap_d is None:
                arxiv_series.append(0)
                hn_series.append(0)
                continue
            match = next(
                (tt for tt in snap_d.trends if tt.canonical_form == t.canonical_form),
                None,
            )
            arxiv_series.append(match.sources.arxiv_30d if match else 0)
            hn_series.append(match.sources.hn_posts_7d if match else 0)
        arxiv_series.append(t.sources.arxiv_30d)
        hn_series.append(t.sources.hn_posts_7d)
        t.still_early_gate = leadlag.still_early_gate(arxiv_series, hn_series)

    # ---- 8d. Top questions (audit 3.14) — mine HN comments per topic ----
    hn_text_corpus = [
        " ".join([p.title or ""] + [c.text or "" for c in (p.comments or [])])
        for p in posts
    ]
    for t in trends:
        topic_qs: list[str] = []
        for needle in _topic_match_strings_for_form(t):
            qs = question_mining.top_questions_for_term(
                hn_text_corpus, term=needle, top_n=5
            )
            topic_qs.extend(qs)
            if len(topic_qs) >= 5:
                break
        t.top_questions = list(dict.fromkeys(topic_qs))[:5]

    # ---- 9. Claude enrichment (opt-in, behind cost cap — audit 1.2) ----
    perplexity_spent_cents: float = 0.0
    if use_claude:
        estimated_cents = summarize.estimate_batch_cost_cents(len(trends))
        # Phase 3 — demand clusters fire one Haiku Batch request per
        # HDBSCAN cluster (≤12 clusters). Fold its estimate into the cap
        # gate so the operator sees the full Claude bill, not a surprise
        # second batch later.
        demand_estimated_cents = demand_mod.estimate_demand_batch_cost_cents(12)
        total_claude_estimate = estimated_cents + demand_estimated_cents
        if max_cost_cents is not None and total_claude_estimate > max_cost_cents:
            log(
                "claude_cost_cap_exceeded",
                level="error",
                estimated_cents=round(total_claude_estimate, 2),
                claude_card_cents=round(estimated_cents, 2),
                demand_batch_cents=round(demand_estimated_cents, 2),
                cap_cents=round(max_cost_cents, 2),
                num_cards=len(trends),
            )
            sys.exit(3)
        trends = _maybe_enrich_with_claude(trends, niche=niche)

        # ---- 9b. Perplexity Sonar pain-point enrichment (Wave 5) ----
        # Folds into the same `--max-cost-cents` gate as Claude. Remaining
        # budget after Claude's estimate (cards + demand batch) is what's
        # left for Sonar; if the cap is None, run uncapped. Per-trend
        # failures degrade silently.
        perplexity_budget_cents = (
            None
            if max_cost_cents is None
            else max(0.0, max_cost_cents - total_claude_estimate)
        )
        trends, perplexity_spent_cents = _maybe_enrich_with_perplexity(
            trends, budget_cents=perplexity_budget_cents
        )
        log(
            "perplexity_enrichment_done",
            level="info",
            trends_enriched=sum(1 for t in trends if t.pain_points),
            spent_cents=round(perplexity_spent_cents, 2),
        )

    # ---- 10. Predictions update ----
    preds = predict.load_predictions(predictions_log)
    current_lifecycles = {t.keyword: t.lifecycle_stage for t in trends}
    updated_preds = predict.update_all_verdicts(
        preds, current_lifecycles_by_keyword=current_lifecycles, today=today_d
    )
    # Invariant: update_all_verdicts neither adds nor removes rows (audit 1.8).
    assert len(updated_preds) == len(preds), (
        f"prediction count drift: loaded {len(preds)} but updated to {len(updated_preds)}"
    )
    hit_rate = predict.compute_hit_rate(updated_preds)
    past_predictions = [p for p in updated_preds if p.verdict != "pending"]
    stuck_pending = [p for p in updated_preds if p.verdict == "pending"]

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
        # Phase 3 — HDBSCAN comment clustering is the product wedge.
        # sync_probe=True fires ONE non-batch Claude call before the paid
        # batch so we can confirm the prompt + schema produce a useful
        # DemandCluster before spending on N more requests (Karpathy §5).
        # Per-cluster failures degrade silently (cluster dropped); a wholly
        # bad probe still allows the batch to run and may rescue the day.
        demand_started = time.time()
        try:
            # Pass the top 10 trend keywords as fallback. On sparse-data days
            # (typical HN with hydrate_top_n=10) HDBSCAN may yield only 1-2
            # clusters; the legacy per-trend Sonnet path backfills toward
            # the 6-12 target so the wedge always ships something.
            demand_clusters = demand_mod.mine_demand_clusters_from_comments(
                posts,
                niche=niche,
                max_clusters=12,
                sync_probe=True,
                fallback_trend_keywords=[t.keyword for t in trends[:10]],
            )
            log(
                "demand_clusters_mined",
                level="info",
                count=len(demand_clusters),
                elapsed_seconds=round(time.time() - demand_started, 2),
            )
        except Exception as e:  # pragma: no cover — defensive net
            log(
                "demand_mining_failed",
                level="warning",
                error=str(e),
                elapsed_seconds=round(time.time() - demand_started, 2),
            )
            demand_clusters = []
    else:
        briefing = _placeholder_briefing()
        demand_clusters = []

    # ---- 12. Assemble + write ----
    # Audit 3.9 — Brier + reliability bins over past predictions for the
    # frontend's forecast confidence band.
    calibration_summary = calibration.compute_calibration_summary(past_predictions)

    # Map fetched newsletter signals into the contract type.
    ns_payload = [
        NewsletterSignal(
            url=ns.url,
            unique_newsletters_count=ns.unique_newsletters_count,
            newsletters=list(ns.newsletters),
            first_seen=ns.first_seen,
            last_seen=ns.last_seen,
        )
        for ns in (newsletter_signals or [])
    ]

    snap = Snapshot(
        snapshot_date=today_d,
        generated_at=datetime.now(tz=timezone.utc),
        trends=trends,
        demand_clusters=demand_clusters,
        briefing=briefing,
        hit_rate=hit_rate,
        past_predictions=past_predictions[-90:],
        newsletter_signals=ns_payload,
        youtube_outliers=youtube_outliers_payload,
        cluster_centroids=canonical_centroids,
        meta={
            "pipeline_runtime_seconds": round(time.time() - started, 2),
            "fetch_seconds": fetch_seconds,
            "sources": {
                "arxiv": {"fetched": len(papers), "ok": fetch_health["arxiv"]},
                "github": {"fetched": len(repos), "ok": fetch_health["github"]},
                "hackernews": {"fetched": len(posts), "ok": fetch_health["hackernews"]},
                "semantic_scholar": {
                    "fetched": len(s2_data or {}),
                    "ok": fetch_health["semantic_scholar"],
                },
                "huggingface": {
                    "fetched": len(hf_models),
                    "ok": fetch_health["huggingface"],
                },
                "reddit": {"fetched": len(reddit_posts), "ok": fetch_health["reddit"]},
                "producthunt": {
                    "fetched": len(producthunt_launches),
                    "ok": fetch_health["producthunt"],
                },
                "replicate": {
                    "fetched": len(replicate_models),
                    "ok": fetch_health["replicate"],
                },
                "bluesky": {"fetched": sum(bluesky_counts.values())},
                "newsletters": {"fetched": len(newsletter_signals or [])},
            },
            "trends_processed": len(trends),
            "use_claude": use_claude,
            "history_days_loaded": len(history),
            "prediction_calibration": calibration_summary,
            "predictions_on_disk": len(preds),
            "predictions_pending_unmatched": len(stuck_pending),
            # v0.2.0 — persist per-model lifetime downloads so the next-day
            # snapshot can compute downloads_7d_delta against this baseline.
            "hf_downloads": {m.id: m.downloads for m in hf_models},
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
    parser.add_argument(
        "--max-cost-cents",
        type=float,
        default=None,
        help="Audit 1.2 — abort with exit code 3 if the estimated batch cost exceeds this cap.",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env.local", override=True)
    snap = main(use_claude=args.claude, max_cost_cents=args.max_cost_cents)
    print(
        f"snapshot written: {len(snap.trends)} trends, "
        f"{len(snap.demand_clusters)} demand clusters, "
        f"{len(snap.past_predictions)} past predictions; "
        f"runtime {snap.meta['pipeline_runtime_seconds']}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
