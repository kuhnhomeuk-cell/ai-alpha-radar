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
    # Audit 3.3 — Reddit (re-added; was removed in 1.4 pre-fetcher)
    reddit_mentions_7d: int = 0
    # Audit 3.4 — Product Hunt featured launches per term
    producthunt_launches_7d: int = 0


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


class Trend(BaseModel):
    keyword: str
    canonical_form: str
    cluster_id: int
    cluster_label: str
    meta_trend: Optional[str] = None
    reddit_top_subreddit: Optional[str] = None
    sources: SourceCounts
    velocity_score: float
    velocity_acceleration: float
    velocity_significance: float = 0.0
    burst_score: float = 0.0
    rrf_score: float = 0.0
    novelty_score: float = 0.0
    still_early_gate: bool = False
    saturation: float
    hidden_gem_score: float
    builder_signal: float
    lifecycle_stage: LifecycleStage
    tbts: int
    convergence: ConvergenceEvent
    summary: str
    summary_confidence: Literal["high", "medium", "low"]
    angles: CreatorAngles
    risk: RiskFlag
    prediction: Prediction
    sparkline_14d: list[int]


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


class Snapshot(BaseModel):
    snapshot_date: date
    generated_at: datetime
    trends: list[Trend]
    demand_clusters: list[DemandCluster]
    briefing: DailyBriefing
    hit_rate: HitRate
    past_predictions: list[Prediction]
    meta: dict[str, Any]
    data_freshness_status: Literal["live", "stale", "error"] = "live"
    # Per-cluster centroid vectors in UMAP reduced space. Keyed by stable
    # cluster_id (audit 2.6). Empty when fewer terms than HDBSCAN's floor.
    cluster_centroids: dict[int, list[float]] = {}
    # Audit 3.2 — URLs surfaced across curated AI newsletters.
    newsletter_signals: list[NewsletterSignal] = []
