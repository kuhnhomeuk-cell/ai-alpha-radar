"""Daily pipeline orchestrator.

Per BACKEND_BUILD §7 Step 12 — wires all upstream modules into one run that
produces a valid public/data.json.

Day-1 reality: with no snapshot history, velocity/acceleration/sparkline
default to zero. Saturation is computed from today's per-source percentiles
across the top-N candidate terms. Convergence fires when a term shows up
in >=3 sources today (window collapses to 0h).

Claude enrichment is OPT-IN (use_claude=True). The default `python -m
pipeline.run` produces a Snapshot with placeholder summary/angles/risk —
sufficient to verify the orchestration structure without burning budget.
A full live run lives behind --claude.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from pipeline import burst
from pipeline import calibration
from pipeline import changepoint
from pipeline import cluster as cluster_mod
from pipeline import cluster_identity
from pipeline import cold_start
from pipeline import demand as demand_mod
from pipeline import leadlag
from pipeline import meta_trends
from pipeline import novelty as novelty_mod
from pipeline import predict, questions as question_mining, rrf, score, snapshot, summarize
from pipeline.fetch import (
    arxiv,
    bluesky,
    github,
    hackernews,
    huggingface,
    newsletters,
    producthunt as producthunt_fetcher,
    reddit as reddit_fetcher,
    replicate as replicate_fetcher,
    semantic_scholar,
)
from pipeline.fetch.producthunt import ProductHuntLaunch
from pipeline.fetch.reddit import RedditPost
from pipeline.fetch.replicate import ReplicateModel
from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost
from pipeline.fetch.huggingface import HFModel
from pipeline.fetch.newsletters import NewsletterSignal
from pipeline.fetch.semantic_scholar import CitationInfo
from pipeline.models import (
    ConvergenceEvent,
    CreatorAngles,
    DailyBriefing,
    LifecycleStage,
    Prediction,
    RiskFlag,
    Snapshot,
    SourceCounts,
    Trend,
)
from pipeline.normalize import Term, extract_candidate_terms

ROOT = Path(__file__).resolve().parent.parent
TOP_N_TRENDS = 30
DEFAULT_NICHE = "AI tools for solo creators"

ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]
ARXIV_LOOKBACK_DAYS = 2
HN_LOOKBACK_DAYS = 7

SPARKLINE_DAYS = 14
VELOCITY_LOOKBACK_DAYS = 30
# Truthfulness gate threshold (audit 1.5). 5 sources active as of 3.1;
# minimum still 3 ok so two can fail without aborting.
MIN_OK_SOURCES = 3

PLACEHOLDER_SUMMARY = "(awaiting Claude enrichment)"
PLACEHOLDER_ANGLE = "(awaiting Claude enrichment)"
PLACEHOLDER_BRIEFING_TEXT = "Daily Movers Briefing pending Claude enrichment."


# ---------- helpers ----------


def _total_mentions(term: Term) -> int:
    return term.arxiv_mentions + term.hn_mentions + term.github_mentions


def _percentile_ranks(values: list[int]) -> list[float]:
    """Cheap percentile rank: each value's position in the sorted ascending order /
    (n-1) * 100. Ties get the same rank. Zeros stay at 0."""
    if not values:
        return []
    sorted_vals = sorted(set(values))
    rank_by_value = {v: i / (len(sorted_vals) - 1) * 100 if len(sorted_vals) > 1 else 0.0 for i, v in enumerate(sorted_vals)}
    return [rank_by_value[v] for v in values]


def _detect_convergence_today(
    term: Term, has_s2: bool, today: datetime
) -> ConvergenceEvent:
    """Day-1 convergence: count which sources fired non-zero mentions for the term."""
    appearances: dict = {}
    if term.arxiv_mentions > 0:
        appearances["arxiv"] = today
    if term.hn_mentions > 0:
        appearances["hackernews"] = today
    if term.github_mentions > 0:
        appearances["github"] = today
    if has_s2:
        appearances["semantic_scholar"] = today
    return score.detect_convergence(appearances)


def _build_source_counts(
    term: Term,
    *,
    s2_citations_7d: int = 0,
    hf_likes: int = 0,
    hf_downloads: int = 0,
    bluesky_count: int = 0,
    reddit_count: int = 0,
    producthunt_count: int = 0,
    replicate_delta: int = 0,
) -> SourceCounts:
    return SourceCounts(
        arxiv_30d=term.arxiv_mentions,
        github_repos_7d=term.github_mentions,
        github_stars_7d=0,  # warm-up; star velocity needs day-2+ snapshots
        hn_posts_7d=term.hn_mentions,
        hn_points_7d=0,  # warm-up; aggregate later
        semantic_scholar_citations_7d=s2_citations_7d,
        huggingface_likes_7d=hf_likes,
        huggingface_downloads_7d=hf_downloads,
        huggingface_spaces_7d=0,  # /api/spaces not yet wired
        bluesky_mentions_7d=bluesky_count,
        reddit_mentions_7d=reddit_count,
        producthunt_launches_7d=producthunt_count,
        replicate_runs_7d_delta=replicate_delta,
    )


def _hf_per_term(
    hf_models: list[HFModel], terms: list[Term]
) -> dict[str, dict[str, int]]:
    """For each term, sum likes/downloads across HF models matching a raw form."""
    if not hf_models:
        return {}
    # Pre-lowercase model text once.
    model_texts = [(m, huggingface.model_text(m).lower()) for m in hf_models]
    out: dict[str, dict[str, int]] = {}
    for term in terms:
        likes = 0
        downloads = 0
        for m, text in model_texts:
            if any(raw.lower() in text for raw in term.raw_forms):
                likes += m.likes
                downloads += m.downloads
        if likes or downloads:
            out[term.canonical_form] = {"likes": likes, "downloads": downloads}
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
    from datetime import timedelta

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


def _s2_citations_by_term(
    papers: list[Paper], terms: list[Term], s2_data: dict[str, CitationInfo]
) -> dict[str, int]:
    """For each canonical term, sum citation_count over papers whose text contains a raw form.

    Returns {canonical_form: int}. The S2 API only exposes a total citation
    count, not a 7-day window — populating `semantic_scholar_citations_7d`
    with the total is a deliberate misnomer flagged for a later rename.
    """
    if not s2_data:
        return {}
    # Pre-lowercase paper text once.
    paper_text = [(p.id, (p.title + " " + p.abstract).lower()) for p in papers]
    out: dict[str, int] = {}
    for term in terms:
        total = 0
        for raw in term.raw_forms:
            needle = raw.lower()
            for pid, text in paper_text:
                if needle in text and pid in s2_data:
                    total += s2_data[pid].citation_count
                    break  # one match per (term, paper) — don't double-count across raw_forms
        if total:
            out[term.canonical_form] = total
    return out


def _trend_total_mentions(t: Trend) -> int:
    """Aggregate per-day mention count stored in a Snapshot's Trend."""
    return (
        t.sources.arxiv_30d
        + t.sources.github_repos_7d
        + t.sources.hn_posts_7d
    )


def _load_history(
    public_dir: Path, today: date, days: int
) -> dict[date, Snapshot]:
    """Read prior dated snapshots from public_dir/snapshots/.

    Corrupt or missing files are skipped silently (logged to stderr). The
    returned dict only contains successfully-parsed snapshots.
    """
    history: dict[date, Snapshot] = {}
    for i in range(1, days + 1):
        d = today - timedelta(days=i)
        try:
            snap = snapshot.read_prior_snapshot(d, public_dir=public_dir)
        except Exception as e:
            print(f"prior snapshot read failed for {d}: {e}", file=sys.stderr)
            continue
        if snap is not None:
            history[d] = snap
    return history


def _keyword_daily_counts(
    history: dict[date, Snapshot], keyword: str, today: date, days: int
) -> list[int]:
    """Return [count_per_day] over the last `days` days (chronological, ending yesterday).

    Days without a snapshot or without the keyword contribute 0.
    """
    series: list[int] = []
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        snap = history.get(d)
        count = 0
        if snap is not None:
            for t in snap.trends:
                if t.keyword == keyword or t.canonical_form == keyword:
                    count = _trend_total_mentions(t)
                    break
        series.append(count)
    return series


def _keyword_daily_counts_for_source(
    history: dict[date, Snapshot],
    keyword: str,
    today: date,
    days: int,
    source_attr: str,
) -> list[int]:
    """Per-source variant — pulls a specific SourceCounts attribute per day."""
    series: list[int] = []
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        snap = history.get(d)
        count = 0
        if snap is not None:
            for t in snap.trends:
                if t.keyword == keyword or t.canonical_form == keyword:
                    count = getattr(t.sources, source_attr, 0)
                    break
        series.append(count)
    return series


def _prior_velocity(
    history: dict[date, Snapshot], keyword: str, today: date
) -> float:
    """Look up yesterday's velocity_score for keyword, default 0.0."""
    snap = history.get(today - timedelta(days=1))
    if snap is None:
        return 0.0
    for t in snap.trends:
        if t.keyword == keyword or t.canonical_form == keyword:
            return t.velocity_score
    return 0.0


def _build_trend(
    term: Term,
    *,
    today: date,
    saturation_pct: float,
    builder_signal: float,
    cluster_id: int,
    cluster_label: str,
    convergence: ConvergenceEvent,
    s2_citations_7d: int = 0,
    hf_likes: int = 0,
    hf_downloads: int = 0,
    bluesky_count: int = 0,
    reddit_count: int = 0,
    reddit_top: Optional[str] = None,
    producthunt_count: int = 0,
    replicate_runs_delta: int = 0,
    top_questions: Optional[list[str]] = None,
    rrf: float = 0.0,
    novelty: float = 0.0,
    meta_trend: Optional[str] = None,
    history: Optional[dict[date, Snapshot]] = None,
    prior_alpha: Optional[float] = None,
    prior_beta: Optional[float] = None,
) -> Trend:
    sources = _build_source_counts(
        term,
        s2_citations_7d=s2_citations_7d,
        hf_likes=hf_likes,
        hf_downloads=hf_downloads,
        bluesky_count=bluesky_count,
        reddit_count=reddit_count,
        producthunt_count=producthunt_count,
        replicate_delta=replicate_runs_delta,
    )
    history = history or {}
    today_count = _total_mentions(term)
    sparkline = _keyword_daily_counts(
        history, term.canonical_form, today, SPARKLINE_DAYS
    )
    prior_30d_total = sum(
        _keyword_daily_counts(
            history, term.canonical_form, today, VELOCITY_LOOKBACK_DAYS
        )
    )
    if history:
        velocity_score = score.velocity(
            today_count,
            prior_30d_total,
            prior_alpha=prior_alpha,
            prior_beta=prior_beta,
        )
        # PELT acceleration replaces the day-over-day delta (audit 2.3).
        velocity_acceleration = changepoint.velocity_acceleration(
            sparkline, today_count=today_count
        )
    else:
        velocity_score = 0.0
        velocity_acceleration = 0.0
    # Mann-Kendall significance over the 14d sparkline (including today).
    velocity_significance = abs(
        score.mann_kendall_confidence(sparkline + [today_count])
    )
    # Kleinberg burst score over the 30d window (or as much as we have).
    burst_window = _keyword_daily_counts(
        history, term.canonical_form, today, VELOCITY_LOOKBACK_DAYS
    ) + [today_count]
    burst_score_val = burst.burst_score(burst_window)
    # Granger-based still-early gate (audit 3.8). Per-source 30-day series.
    arxiv_series = _keyword_daily_counts_for_source(
        history, term.canonical_form, today, VELOCITY_LOOKBACK_DAYS, "arxiv_30d"
    ) + [term.arxiv_mentions]
    hn_series = _keyword_daily_counts_for_source(
        history, term.canonical_form, today, VELOCITY_LOOKBACK_DAYS, "hn_posts_7d"
    ) + [term.hn_mentions]
    still_early = leadlag.still_early_gate(arxiv_series, hn_series)
    hidden_gem_score = score.hidden_gem(velocity_score, saturation_pct, builder_signal)
    lifecycle = score.lifecycle_stage(
        arxiv_30d=sources.arxiv_30d,
        github_repos_7d=sources.github_repos_7d,
        hn_points_7d=sources.hn_points_7d,
        saturation=saturation_pct,
        velocity=velocity_score,
        builder_signal=builder_signal,
        velocity_significance=velocity_significance,
    )
    tbts_score = score.tbts(
        velocity_score=velocity_score,
        hidden_gem_score=hidden_gem_score,
        lifecycle=lifecycle,
        convergence_detected=convergence.detected,
    )
    return Trend(
        keyword=term.canonical_form,
        canonical_form=term.canonical_form,
        cluster_id=cluster_id,
        cluster_label=cluster_label,
        meta_trend=meta_trend,
        reddit_top_subreddit=reddit_top,
        top_questions=top_questions or [],
        sources=sources,
        velocity_score=velocity_score,
        velocity_acceleration=velocity_acceleration,
        velocity_significance=velocity_significance,
        burst_score=burst_score_val,
        rrf_score=rrf,
        novelty_score=novelty,
        still_early_gate=still_early,
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
        prediction=_placeholder_prediction(term.canonical_form, today),
        sparkline_14d=sparkline,
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
            related_terms=[],
            arxiv_papers_7d=t.sources.arxiv_30d,
            github_repos_7d=t.sources.github_repos_7d,
            hn_posts_7d=t.sources.hn_posts_7d,
            s2_citations_7d=t.sources.semantic_scholar_citations_7d,
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
    hf_models: Optional[list[HFModel]] = None,
    newsletter_signals: Optional[list[NewsletterSignal]] = None,
    reddit_posts: Optional[list[RedditPost]] = None,
    producthunt_launches: Optional[list[ProductHuntLaunch]] = None,
    replicate_models: Optional[list[ReplicateModel]] = None,
    s2_data: Optional[dict[str, CitationInfo]] = None,
    use_claude: bool = False,
    max_cost_cents: Optional[float] = None,
    public_dir: Path = ROOT / "public",
    predictions_log: Path = ROOT / "data" / "predictions.jsonl",
    corpus_centroid_path: Optional[Path] = None,
    bluesky_db_path: Optional[Path] = None,
    niche: str = DEFAULT_NICHE,
) -> Snapshot:
    started = time.time()
    today_d = today or date.today()
    today_dt = datetime.combine(today_d, datetime.min.time()).replace(tzinfo=timezone.utc)

    # ---- 0. Load prior snapshots for velocity / sparkline math ----
    history = _load_history(public_dir, today_d, VELOCITY_LOOKBACK_DAYS)
    is_cold_start = len(history) == 0
    # Empirical Beta(α, β) prior for low-count smoothing — fitted across
    # every term-day observed in history.
    historical_term_day_counts = [
        _trend_total_mentions(t)
        for snap in history.values()
        for t in snap.trends
    ]
    prior_alpha, prior_beta = cold_start.compute_empirical_prior(
        historical_term_day_counts
    )

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

    # Hugging Face Hub trending — no auth, free public endpoint.
    if hf_models is None:
        try:
            hf_models = huggingface.fetch_trending_models()
        except Exception as e:
            print(f"huggingface fetch failed: {e}", file=sys.stderr)
            hf_models = []
    fetch_health["huggingface"] = len(hf_models) > 0

    # Newsletter RSS cross-mentions — no auth, curated feed list.
    if newsletter_signals is None:
        try:
            newsletter_signals = newsletters.fetch_newsletter_signals(
                today=today_dt
            )
        except Exception as e:
            print(f"newsletter fetch failed: {e}", file=sys.stderr)
            newsletter_signals = []

    # Reddit — needs creds; empty list on miss.
    if reddit_posts is None:
        try:
            reddit_posts = reddit_fetcher.fetch_top_posts()
        except Exception as e:
            print(f"reddit fetch failed: {e}", file=sys.stderr)
            reddit_posts = []
    fetch_health["reddit"] = len(reddit_posts) > 0

    # Product Hunt — needs PRODUCT_HUNT_TOKEN; empty on miss.
    if producthunt_launches is None:
        try:
            producthunt_launches = producthunt_fetcher.fetch_trending_launches()
        except Exception as e:
            print(f"producthunt fetch failed: {e}", file=sys.stderr)
            producthunt_launches = []
    fetch_health["producthunt"] = len(producthunt_launches) > 0

    # Replicate — needs REPLICATE_API_KEY; empty on miss.
    if replicate_models is None:
        try:
            replicate_models = replicate_fetcher.fetch_trending()
        except Exception as e:
            print(f"replicate fetch failed: {e}", file=sys.stderr)
            replicate_models = []
    fetch_health["replicate"] = len(replicate_models) > 0

    # Semantic Scholar enrichment — runs against arxiv ids we just fetched.
    if s2_data is None:
        if papers:
            try:
                s2_data = semantic_scholar.enrich_papers(
                    [p.id for p in papers],
                    api_key=os.environ.get("SEMANTIC_SCHOLAR_KEY") or None,
                )
            except Exception as e:
                print(f"semantic scholar fetch failed: {e}", file=sys.stderr)
                s2_data = {}
        else:
            s2_data = {}
    fetch_health["semantic_scholar"] = bool(s2_data)
    fetch_seconds = round(time.time() - fetch_started, 2)

    # Truthfulness gate — refuse to ship a snapshot built on <3 live sources.
    ok_sources = sum(1 for h in fetch_health.values() if h)
    if ok_sources < MIN_OK_SOURCES:
        failed = sorted(k for k, v in fetch_health.items() if not v)
        print(
            f"FATAL: {ok_sources}/{len(fetch_health)} sources ok "
            f"(failed: {failed}); aborting without writing data.json",
            file=sys.stderr,
        )
        sys.exit(2)

    # ---- 2. Normalize ----
    terms = extract_candidate_terms(papers, posts, repos, hf_models=hf_models)

    # ---- 3. Top-N selection ----
    top_terms = sorted(terms, key=_total_mentions, reverse=True)[:TOP_N_TRENDS]
    if not top_terms:
        # Empty universe — write an empty snapshot and exit cleanly
        snap = Snapshot(
            snapshot_date=today_d,
            generated_at=datetime.now(tz=timezone.utc),
            trends=[],
            demand_clusters=[],
            briefing=_placeholder_briefing(),
            hit_rate=predict.compute_hit_rate([]),
            past_predictions=[],
            meta={
                "empty": True,
                "fetch_seconds": fetch_seconds,
                "cold_start": is_cold_start,
                "history_days_loaded": len(history),
            },
        )
        snapshot.write_snapshot(snap, public_dir=public_dir)
        return snap

    # ---- 4. Cluster ----
    raw_assignments, raw_centroids, term_embeddings = (
        cluster_mod.cluster_terms_with_centroids(
            [t.canonical_form for t in top_terms]
        )
    )
    # Diachronic novelty (audit 3.10): cosine distance to rolling corpus centroid.
    import numpy as np

    centroid_path = (
        corpus_centroid_path
        if corpus_centroid_path is not None
        else novelty_mod.DEFAULT_CENTROID_PATH
    )
    prior_corpus_centroid = novelty_mod.load_centroid(centroid_path)
    if term_embeddings:
        emb_matrix = np.array([term_embeddings[t.canonical_form] for t in top_terms if t.canonical_form in term_embeddings])
        today_centroid = novelty_mod.compute_centroid(emb_matrix)
        novelty_by_term = {
            t.canonical_form: novelty_mod.cosine_distance(
                np.array(term_embeddings[t.canonical_form]),
                prior_corpus_centroid if prior_corpus_centroid is not None else today_centroid,
            )
            for t in top_terms
            if t.canonical_form in term_embeddings
        }
        updated_corpus_centroid = novelty_mod.update_rolling_centroid(
            prior_corpus_centroid, today_centroid
        )
        novelty_mod.save_centroid(updated_corpus_centroid, centroid_path)
    else:
        novelty_by_term = {}
    # Canonicalize today's HDBSCAN ids against yesterday's centroids so
    # cluster_id is stable across snapshots (audit 2.6).
    yesterday = today_d - timedelta(days=1)
    prior_snap = history.get(yesterday)
    prior_centroids_arr: dict[int, "np.ndarray"] = {}
    if prior_snap is not None and prior_snap.cluster_centroids:
        import numpy as np

        prior_centroids_arr = {
            int(k): np.array(v) for k, v in prior_snap.cluster_centroids.items()
        }
    import numpy as np

    new_centroids_arr = {
        cid: np.array(vec) for cid, vec in raw_centroids.items()
    }
    raw_labels = {
        ca.cluster_id: ca.cluster_label for ca in raw_assignments.values()
    }
    id_remap = cluster_identity.canonicalize_cluster_ids(
        new_centroids_arr,
        prior_centroids_arr,
        labels_by_new_id=raw_labels,
    )
    cluster_assignments = {
        term: cluster_mod.ClusterAssignment(
            cluster_id=id_remap.get(ca.cluster_id, ca.cluster_id),
            cluster_label=ca.cluster_label,
        )
        for term, ca in raw_assignments.items()
    }
    canonical_centroids: dict[int, list[float]] = {
        id_remap[cid]: vec for cid, vec in raw_centroids.items() if cid in id_remap
    }

    # Meta-trends — 2nd-pass HDBSCAN over the canonical centroids (audit 3.13).
    cluster_to_meta = meta_trends.cluster_centroids(canonical_centroids)
    canonical_cluster_labels = {
        ca.cluster_id: ca.cluster_label for ca in cluster_assignments.values()
    }
    meta_labels = meta_trends.build_meta_trend_labels(
        cluster_to_meta, canonical_cluster_labels
    )

    # ---- 5. Score (per-source percentiles → saturation) ----
    arxiv_pcts = _percentile_ranks([t.arxiv_mentions for t in top_terms])
    hn_pcts = _percentile_ranks([t.hn_mentions for t in top_terms])
    gh_pcts = _percentile_ranks([t.github_mentions for t in top_terms])

    max_github_for_builder_signal = max((t.github_mentions for t in top_terms), default=1) or 1

    # ---- 6. Build trends (placeholder Claude outputs) ----
    s2_by_term = _s2_citations_by_term(papers, top_terms, s2_data)
    hf_by_term = _hf_per_term(hf_models, top_terms)
    # Bluesky firehose mentions — reader only; subscriber runs separately.
    bsky_path = bluesky_db_path if bluesky_db_path is not None else bluesky.DEFAULT_DB_PATH
    bluesky_counts = bluesky.read_mention_counts(
        bsky_path,
        keywords={t.canonical_form for t in top_terms},
        since=today_dt - timedelta(days=7),
    )
    # Reddit per-term counts + top-subreddit (audit 3.3).
    reddit_keyword_terms = [t.canonical_form for t in top_terms]
    reddit_mentions = reddit_fetcher.mentions_per_term(
        reddit_posts, terms=reddit_keyword_terms
    )
    reddit_tops = reddit_fetcher.top_subreddit_per_term(
        reddit_posts, terms=reddit_keyword_terms
    )
    # Product Hunt per-term launches (audit 3.4).
    ph_launches_by_term = producthunt_fetcher.launches_per_term(
        producthunt_launches, terms=reddit_keyword_terms
    )
    # Question mining (audit 3.14) — drains HN story/comment text + Reddit
    # title/selftext into a single text pool per term.
    question_texts: list[str] = []
    for p in posts:
        question_texts.append(p.title)
        if p.story_text:
            question_texts.append(p.story_text)
        for c in (p.comments or []):
            question_texts.append(c.text)
    for r in reddit_posts:
        question_texts.append(r.title)
        if r.selftext:
            question_texts.append(r.selftext)
    # Replicate per-term run delta (audit 3.5). Persist today's totals so the
    # next run can compute a delta against this one.
    today_replicate_counts = {
        f"{m.owner}/{m.name}": m.run_count for m in replicate_models
    }
    prior_replicate_counts = replicate_fetcher.load_prior_run_counts()
    replicate_deltas = replicate_fetcher.run_count_deltas(
        today_replicate_counts, prior_replicate_counts
    )
    # Build a synthetic ReplicateModel-like list where run_count is the *delta*
    # so runs_per_term aggregates growth rather than total volume.
    delta_models = [
        replicate_fetcher.ReplicateModel(
            owner=m.owner,
            name=m.name,
            description=m.description,
            visibility=m.visibility,
            run_count=replicate_deltas.get(f"{m.owner}/{m.name}", 0),
        )
        for m in replicate_models
    ]
    replicate_by_term = replicate_fetcher.runs_per_term(
        delta_models, terms=reddit_keyword_terms
    )
    if today_replicate_counts:
        replicate_fetcher.save_run_counts(today_replicate_counts)
    # Reciprocal Rank Fusion across per-source counts (audit 3.7).
    rrf_input = {
        "arxiv": rrf.ranks_from_counts(
            {t.canonical_form: t.arxiv_mentions for t in top_terms}
        ),
        "github": rrf.ranks_from_counts(
            {t.canonical_form: t.github_mentions for t in top_terms}
        ),
        "hackernews": rrf.ranks_from_counts(
            {t.canonical_form: t.hn_mentions for t in top_terms}
        ),
        "huggingface": rrf.ranks_from_counts(
            {t.canonical_form: t.huggingface_mentions for t in top_terms}
        ),
        "s2": rrf.ranks_from_counts(s2_by_term),
    }
    rrf_by_term = rrf.rrf_score(rrf_input)
    trends: list[Trend] = []
    for i, term in enumerate(top_terms):
        builder_sig = term.github_mentions / max_github_for_builder_signal
        saturation_pct = score.saturation(
            github=gh_pcts[i], hn=hn_pcts[i], arxiv=arxiv_pcts[i], semantic_scholar=0.0
        )
        s2_citations = s2_by_term.get(term.canonical_form, 0)
        hf_agg = hf_by_term.get(term.canonical_form, {"likes": 0, "downloads": 0})
        convergence = _detect_convergence_today(
            term, has_s2=s2_citations > 0, today=today_dt
        )
        ca = cluster_assignments.get(term.canonical_form)
        cluster_id = ca.cluster_id if ca else -1
        cluster_label = ca.cluster_label if ca else "Unclustered Emerging"
        meta_id = cluster_to_meta.get(cluster_id, -1)
        meta_trend_label = meta_labels.get(meta_id) if meta_id != -1 else None
        trends.append(
            _build_trend(
                term,
                today=today_d,
                saturation_pct=saturation_pct,
                builder_signal=builder_sig,
                cluster_id=cluster_id,
                cluster_label=cluster_label,
                convergence=convergence,
                s2_citations_7d=s2_citations,
                hf_likes=hf_agg["likes"],
                hf_downloads=hf_agg["downloads"],
                bluesky_count=bluesky_counts.get(term.canonical_form, 0),
                reddit_count=reddit_mentions.get(term.canonical_form, 0),
                reddit_top=reddit_tops.get(term.canonical_form),
                producthunt_count=ph_launches_by_term.get(term.canonical_form, 0),
                replicate_runs_delta=replicate_by_term.get(term.canonical_form, 0),
                top_questions=question_mining.top_questions_for_term(
                    question_texts, term=term.canonical_form, top_n=5
                ),
                rrf=rrf_by_term.get(term.canonical_form, 0.0),
                novelty=novelty_by_term.get(term.canonical_form, 0.0),
                meta_trend=meta_trend_label,
                history=history,
                prior_alpha=prior_alpha,
                prior_beta=prior_beta,
            )
        )

    # ---- 7. Claude enrichment (opt-in, behind cost cap) ----
    if use_claude:
        estimated_cents = summarize.estimate_batch_cost_cents(len(trends))
        if max_cost_cents is not None and estimated_cents > max_cost_cents:
            print(
                f"FATAL: estimated Claude cost {estimated_cents:.2f}c > cap "
                f"{max_cost_cents:.2f}c for {len(trends)} cards; aborting before any paid call",
                file=sys.stderr,
            )
            sys.exit(3)
        trends = _maybe_enrich_with_claude(trends, niche=niche)

    # ---- 8. Predictions update ----
    preds = predict.load_predictions(predictions_log)
    current_lifecycles = {t.keyword: t.lifecycle_stage for t in trends}
    updated_preds = predict.update_all_verdicts(
        preds, current_lifecycles_by_keyword=current_lifecycles, today=today_d
    )
    # Invariant: update_all_verdicts neither adds nor removes rows.
    assert len(updated_preds) == len(preds), (
        f"prediction count drift: loaded {len(preds)} but updated to {len(updated_preds)}"
    )
    hit_rate = predict.compute_hit_rate(updated_preds)
    past_predictions = [p for p in updated_preds if p.verdict != "pending"]
    stuck_pending = [p for p in updated_preds if p.verdict == "pending"]

    # ---- 9. Briefing + demand (opt-in for Claude) ----
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

    # ---- 10. Assemble + write ----
    snap = Snapshot(
        snapshot_date=today_d,
        generated_at=datetime.now(tz=timezone.utc),
        trends=trends,
        demand_clusters=demand_clusters,
        briefing=briefing,
        hit_rate=hit_rate,
        past_predictions=past_predictions[-90:],  # cap retention at 90 entries
        cluster_centroids=canonical_centroids,
        newsletter_signals=newsletter_signals,
        meta={
            "pipeline_runtime_seconds": round(time.time() - started, 2),
            "fetch_seconds": fetch_seconds,
            "sources": {
                "arxiv": {"fetched": len(papers), "ok": fetch_health["arxiv"]},
                "github": {"fetched": len(repos), "ok": fetch_health["github"]},
                "hackernews": {"fetched": len(posts), "ok": fetch_health["hackernews"]},
                "semantic_scholar": {
                    "fetched": len(s2_data),
                    "ok": fetch_health["semantic_scholar"],
                },
                "huggingface": {
                    "fetched": len(hf_models),
                    "ok": fetch_health["huggingface"],
                },
                "reddit": {
                    "fetched": len(reddit_posts),
                    "ok": fetch_health["reddit"],
                },
                "producthunt": {
                    "fetched": len(producthunt_launches),
                    "ok": fetch_health["producthunt"],
                },
                "replicate": {
                    "fetched": len(replicate_models),
                    "ok": fetch_health["replicate"],
                },
            },
            "trends_processed": len(trends),
            "use_claude": use_claude,
            "cold_start": is_cold_start,
            "history_days_loaded": len(history),
            "predictions_on_disk": len(preds),
            "predictions_pending_unmatched": len(stuck_pending),
            "prediction_calibration": calibration.compute_calibration_summary(
                updated_preds
            ),
        },
    )
    snapshot.write_snapshot(snap, public_dir=public_dir)
    return snap


def _cli() -> int:
    parser = argparse.ArgumentParser(description="AI Alpha Radar daily pipeline")
    parser.add_argument(
        "--claude",
        action="store_true",
        help="Enable live Claude enrichment (cost: ~$0.30/day budget cap)",
    )
    parser.add_argument(
        "--max-cost-cents",
        type=float,
        default=None,
        help="Abort with exit code 3 if the estimated batch cost exceeds this cap.",
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
