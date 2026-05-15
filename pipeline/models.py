"""Data contract for the AI Alpha Radar pipeline.

Every field below is part of the frontend interface. Schema changes require a
coordination note to the frontend team.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

LifecycleStage = Literal["whisper", "builder", "creator", "hype", "commodity"]
SourceName = Literal[
    "arxiv",
    "github",
    "hackernews",
    "semantic_scholar",
    "youtube",
    "reddit",
    "huggingface",
    "grok_x",
    "tiktok",
]
PredictionVerdict = Literal["pending", "tracking", "verified", "verified_early", "wrong"]

NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
PercentFloat = Annotated[float, Field(ge=0, le=100)]
UnitFloat = Annotated[float, Field(ge=0, le=1)]
TbtsScore = Annotated[int, Field(ge=0, le=100)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceCounts(ContractModel):
    arxiv_30d: NonNegativeInt = 0
    github_repos_7d: NonNegativeInt = 0
    github_stars_7d: NonNegativeInt = 0
    hn_posts_7d: NonNegativeInt = 0
    hn_points_7d: NonNegativeInt = 0
    semantic_scholar_citations_7d: NonNegativeInt = 0
    youtube_videos_7d: NonNegativeInt = 0
    reddit_mentions_7d: NonNegativeInt = 0
    x_posts_7d: NonNegativeInt = 0
    # v0.2.0 — HuggingFace model adoption signal.
    huggingface_models_7d: NonNegativeInt = 0


class ConvergenceEvent(ContractModel):
    detected: bool
    sources_hit: list[SourceName]
    window_hours: NonNegativeInt
    first_appearance: dict[SourceName, datetime]

    @model_validator(mode="after")
    def _consistent_detection_state(self) -> "ConvergenceEvent":
        if not self.detected:
            if self.sources_hit or self.window_hours != 0 or self.first_appearance:
                raise ValueError("undetected convergence events must have empty source/window data")
            return self
        if len(self.sources_hit) < 3:
            raise ValueError("detected convergence events require at least three sources")
        if self.window_hours <= 0:
            raise ValueError("detected convergence events require a positive window")
        missing = set(self.sources_hit) - set(self.first_appearance)
        if missing:
            raise ValueError("detected convergence sources must have first_appearance timestamps")
        return self


class CreatorAngles(ContractModel):
    hook: str
    contrarian: str
    tutorial: str
    eli_creator: str


class RiskFlag(ContractModel):
    breakout_likelihood: Literal["low", "medium", "high", "breakout"]
    peak_estimate_days: Optional[NonNegativeInt]
    risk_flag: str
    rationale: str


class Prediction(ContractModel):
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


class Trend(ContractModel):
    keyword: str
    canonical_form: str
    cluster_id: int
    cluster_label: str
    sources: SourceCounts
    velocity_score: NonNegativeFloat
    velocity_acceleration: float
    saturation: PercentFloat
    hidden_gem_score: UnitFloat
    builder_signal: UnitFloat
    lifecycle_stage: LifecycleStage
    tbts: TbtsScore
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
    # v0.2.0 — cross-source consensus signal. sources_confirming is the
    # subset of {arxiv, github, hackernews, reddit, huggingface} with at
    # least one attributed doc. consensus_ratio normalizes against the
    # count of sources that fetched ok for this snapshot, so a topic seen
    # on 4 of 5 active sources scores 0.8. Both optional with defaults so
    # snapshots that pre-date them round-trip.
    sources_confirming: list[str] = []
    consensus_ratio: UnitFloat = 0.0


class DemandQuote(ContractModel):
    text: str
    source: str
    raw_url: Optional[str] = None


class DemandCluster(ContractModel):
    question_shape: str
    askers_estimate: NonNegativeInt
    quotes: list[DemandQuote]
    sources: list[SourceName]
    weekly_growth_pct: float
    open_window_days: NonNegativeInt
    creator_brief: str
    related_trends: list[str]


class DailyBriefing(ContractModel):
    text: str
    moved_up: list[str]
    moved_down: list[str]
    emerging: list[str]
    generated_at: datetime


class HitRate(ContractModel):
    rate: UnitFloat
    verified: NonNegativeInt
    tracking: NonNegativeInt
    verified_early: NonNegativeInt
    wrong: NonNegativeInt


class Snapshot(ContractModel):
    snapshot_date: date
    generated_at: datetime
    trends: list[Trend]
    demand_clusters: list[DemandCluster]
    briefing: DailyBriefing
    hit_rate: HitRate
    past_predictions: list[Prediction]
    meta: dict[str, Any]
