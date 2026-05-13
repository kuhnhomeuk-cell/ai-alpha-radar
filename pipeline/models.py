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
    youtube_videos_7d: int = 0
    reddit_mentions_7d: int = 0
    x_posts_7d: int = 0


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


class Trend(BaseModel):
    keyword: str
    canonical_form: str
    cluster_id: int
    cluster_label: str
    sources: SourceCounts
    velocity_score: float
    velocity_acceleration: float
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
