"""Data contract for the AI Alpha Radar pipeline.

Every field below is part of the frontend interface. Schema changes require a
coordination note to the frontend team.
"""

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

LifecycleStage = Literal["whisper", "builder", "creator", "hype", "commodity"]
SourceName = Literal[
    "arxiv",
    "github",
    "hackernews",
    "semantic_scholar",
    "youtube",
    "reddit",
    "grok_x",
    "tiktok",
    "digg",
    "inferred",
]
PredictionVerdict = Literal["pending", "tracking", "verified", "verified_early", "wrong"]


class SourceCounts(BaseModel):
    arxiv_30d: int = 0
    github_repos_7d: int = 0
    github_stars_7d: int = 0
    hn_posts_7d: int = 0
    hn_points_7d: int = 0
    semantic_scholar_citations_7d: int = 0
    # Audit 3.1 — Hugging Face Hub trending signals
    huggingface_likes_7d: int = 0
    huggingface_downloads_7d: int = 0
    huggingface_spaces_7d: int = 0  # populated in a later wave (/api/spaces)
    # Audit 3.6 — Bluesky firehose mention count (operator-scheduled subscriber)
    bluesky_mentions_7d: int = 0
    # Audit 3.3 — Reddit shortlist post-mentions
    reddit_mentions_7d: int = 0
    # Audit 3.4 — Product Hunt featured launches per term
    producthunt_launches_7d: int = 0
    # Audit 3.5 — Replicate model-run-count delta per term
    replicate_runs_7d_delta: int = 0
    # Audit 1.4 — fields retained for back-compat with v0.1.0 snapshots
    youtube_videos_7d: int = 0
    x_posts_7d: int = 0
    # Digg AI cross-reference signal — story-title substring matches against a
    # topic's needles. >0 means this topic was surfaced on digg.com/ai in the
    # last 7 days (Digg = X-influencer engagement aggregator, not crawled web).
    digg_ai_mentions_7d: int = 0


class ConvergenceEvent(BaseModel):
    detected: bool
    sources_hit: list[SourceName]
    window_hours: int
    first_appearance: dict[SourceName, datetime]


class CreatorAngles(BaseModel):
    hook: str
    contrarian: str
    tutorial: str
    eli_creator: str


class RiskFlag(BaseModel):
    breakout_likelihood: Literal["low", "medium", "high", "breakout"]
    peak_estimate_days: Optional[int]
    risk_flag: str
    rationale: str


class Prediction(BaseModel):
    text: str
    filed_at: date
    target_date: date
    verdict: PredictionVerdict
    verdict_text: Optional[str] = None
    verified_at: Optional[date] = None
    # Linkage + criteria for deterministic verdict checking.
    # Optional for backwards compat with the original BACKEND_BUILD §6
    # schema; Step 10 onwards populates them.
    keyword: Optional[str] = None
    lifecycle_at_filing: Optional["LifecycleStage"] = None
    target_lifecycle: Optional["LifecycleStage"] = None


class PainPoint(BaseModel):
    """Wave 5 — a single creator pain point surfaced by Perplexity Sonar.

    Sonar returns these ranked from most important to least important; we
    preserve that order in `rank` (1-based). No `confidence` field — Sonar's
    own ordering is the signal, an additional LLM-derived confidence would be
    noisy and unverifiable.
    """

    text: str
    source_url: str
    source_title: str
    rank: int = 0


class Trend(BaseModel):
    keyword: str
    canonical_form: str
    cluster_id: int
    cluster_label: str
    # Audit 3.13 — parent narrative label across clusters.
    meta_trend: Optional[str] = None
    # Audit 3.3 — top subreddit for this trend (if any).
    reddit_top_subreddit: Optional[str] = None
    # Audit 3.14 — top question-shaped strings mined from HN/Reddit comments.
    top_questions: list[str] = []
    sources: SourceCounts
    velocity_score: float
    velocity_acceleration: float
    # Audit 2.9 — Mann-Kendall p-value over the velocity series.
    velocity_significance: float = 0.0
    # Audit 2.2 — Kleinberg-style burst score, 0-1.
    burst_score: float = 0.0
    # Audit 3.7 — Reciprocal Rank Fusion across ranked algorithms.
    rrf_score: float = 0.0
    # Audit 3.10 — diachronic embedding novelty, 0-1.
    novelty_score: float = 0.0
    # Audit 3.8 — Granger / velocity-gap gate; true if arxiv leads HN.
    still_early_gate: bool = False
    saturation: float
    hidden_gem_score: float
    builder_signal: float
    lifecycle_stage: LifecycleStage
    tbts: float
    convergence: ConvergenceEvent
    summary: str
    summary_confidence: Literal["high", "medium", "low"]
    angles: CreatorAngles
    risk: RiskFlag
    prediction: Prediction
    sparkline_14d: list[int]
    # v0.1.1: topic-extraction additions. Both optional + default empty so
    # older snapshots round-trip. The frontend search box reads aliases;
    # source_doc_ids stays backend-internal until the convergence-event
    # timeline modal (v0.2 Commit 5) consumes it for source attribution.
    aliases: list[str] = []
    source_doc_ids: dict[str, list[str | int]] = {}
    # Wave 5 — Perplexity Sonar pain-point enrichment. Default empty so
    # snapshots written before Wave 5 round-trip unchanged.
    pain_points: list[PainPoint] = []
    # v0.2.0 — cross-source consensus. sources_confirming is the subset of
    # {arxiv, github, hackernews, reddit, huggingface, producthunt,
    # replicate, bluesky, semantic_scholar} that contributed at least one
    # attributed doc to this topic. consensus_ratio normalizes against the
    # count of sources that fetched successfully on this snapshot. Both
    # optional with defaults so pre-v0.2 snapshots round-trip cleanly.
    sources_confirming: list[str] = []
    consensus_ratio: float = 0.0


class DemandQuote(BaseModel):
    text: str
    source: str
    raw_url: Optional[str] = None


class DemandCluster(BaseModel):
    question_shape: str
    askers_estimate: int
    quotes: list[DemandQuote]
    sources: list[SourceName]
    weekly_growth_pct: float
    open_window_days: int
    creator_brief: str
    related_trends: list[str]


class DailyBriefing(BaseModel):
    text: str
    moved_up: list[str]
    moved_down: list[str]
    emerging: list[str]
    generated_at: datetime


class NewsletterSignal(BaseModel):
    """Audit 3.2 — a URL surfaced across multiple curated AI newsletters."""

    url: str
    unique_newsletters_count: int
    newsletters: list[str]
    first_seen: datetime
    last_seen: datetime


class HitRate(BaseModel):
    rate: float
    verified: int
    tracking: int
    verified_early: int
    wrong: int


class YoutubeOutlier(BaseModel):
    """Wave 5 — a YouTube video that's overperforming its channel's baseline.

    `outlier_multiple` is the breakout factor: a video's view count divided
    by the channel's baseline view count over recent uploads. Sourced from
    VidIQ's `breakoutScore` (a value of 5.0 means the video has 5× the
    channel's typical performance).

    Distinct from `SourceCounts.youtube_videos_7d` — that's a per-trend
    count keyed to a topic; `YoutubeOutlier` is per-video metadata for the
    dashboard's outliers route.
    """

    video_id: str
    title: str
    channel_name: str
    view_count: int
    channel_baseline_views: int
    outlier_multiple: float
    published_at: datetime
    thumbnail_url: str
    key_topics: list[str] = []


class Snapshot(BaseModel):
    snapshot_date: date
    generated_at: datetime
    trends: list[Trend]
    demand_clusters: list[DemandCluster]
    briefing: DailyBriefing
    hit_rate: HitRate
    past_predictions: list[Prediction]
    meta: dict[str, Any]
    # Audit 1.6 — staleness banner state for the frontend.
    data_freshness_status: Literal["live", "stale", "error"] = "live"
    # Audit 2.6 — per-cluster centroid vectors in UMAP reduced space, keyed
    # by stable cluster_id. Empty when fewer terms than HDBSCAN's floor.
    cluster_centroids: dict[int, list[float]] = {}
    # Audit 3.2 — URLs surfaced across curated AI newsletters.
    newsletter_signals: list[NewsletterSignal] = []
    # Wave 5 — YouTube outliers (videos overperforming their channel baseline).
    # Populated from data/youtube_outliers.json, an operator-refreshed file
    # written by fanning out VidIQ MCP calls. Default empty so snapshots
    # written before Wave 5 round-trip unchanged.
    youtube_outliers: list[YoutubeOutlier] = []
