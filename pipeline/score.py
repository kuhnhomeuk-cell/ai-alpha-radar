"""Scoring math — the analytical core.

Per BACKEND_BUILD §7 Step 7 + PLAN.md §6. Pure functions, deterministic,
no I/O. Every formula here is hand-verified in tests/test_score.py.

Two spec deviations, both surfaced not silent:

1. The spec wrote `lifecycle_stage(t: Trend) -> ...` and `tbts(t: Trend) ->
   ...`. Trend's own `lifecycle_stage` and `tbts` fields make that
   circular — these functions are *prerequisites* for building a Trend.
   Switched to keyword-only argument signatures that take the relevant
   inputs directly. Same math, no schema coupling.

2. `lifecycle_stage` has no rule for the "in-between" zone (e.g. brand-new
   term with arxiv_30d=0). Default = "whisper" — the lowest engagement
   stage, semantically "we're watching this from a distance."
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pymannkendall as mk

from pipeline.models import ConvergenceEvent, LifecycleStage, SourceName

# Saturation weights — BACKEND_BUILD §6.2
SAT_WEIGHTS = {"github": 0.35, "hn": 0.30, "arxiv": 0.20, "semantic_scholar": 0.15}

# Hidden-gem weights — PLAN.md §6.3
HG_WEIGHTS = {"velocity": 0.40, "saturation_inverse": 0.35, "builder": 0.25}

# Lifecycle weights for TBTS — PLAN.md §6.4
LIFECYCLE_WEIGHTS: dict[LifecycleStage, float] = {
    "whisper": 0.20,
    "builder": 0.50,
    "creator": 0.80,
    "hype": 0.40,
    "commodity": 0.10,
}

# TBTS weights — PLAN.md §6.8
TBTS_WEIGHTS = {
    "velocity": 0.35,
    "hidden_gem": 0.30,
    "lifecycle": 0.20,
    "convergence": 0.15,
}

VELOCITY_CLIP = 10.0  # vnorm = min(velocity, 10) / 10
MENTIONS_30D_FLOOR = 10  # kills low-count inflation per PLAN.md §6.1
CONVERGENCE_WINDOW_HOURS = 72
CONVERGENCE_MIN_SOURCES = 3
MANN_KENDALL_MIN_LENGTH = 4


def velocity(mentions_7d: int, mentions_30d: int) -> float:
    """`mentions_7d / max(mentions_30d/30 * 7, 1)` with mentions_30d floored at 10."""
    floored_30d = max(mentions_30d, MENTIONS_30D_FLOOR)
    expected_7d = floored_30d / 30 * 7
    denom = max(expected_7d, 1.0)
    return mentions_7d / denom


def saturation(
    *, github: float, hn: float, arxiv: float, semantic_scholar: float
) -> float:
    """Weighted blend of per-source percentiles. Returns 0-100."""
    return (
        SAT_WEIGHTS["github"] * github
        + SAT_WEIGHTS["hn"] * hn
        + SAT_WEIGHTS["arxiv"] * arxiv
        + SAT_WEIGHTS["semantic_scholar"] * semantic_scholar
    )


def hidden_gem(
    velocity_score: float, saturation_pct: float, builder_signal: float
) -> float:
    """`0.4*vnorm + 0.35*(1 - sat/100) + 0.25*builder`. Returns 0-1."""
    vnorm = min(velocity_score, VELOCITY_CLIP) / VELOCITY_CLIP
    return (
        HG_WEIGHTS["velocity"] * vnorm
        + HG_WEIGHTS["saturation_inverse"] * (1 - saturation_pct / 100)
        + HG_WEIGHTS["builder"] * builder_signal
    )


def lifecycle_stage(
    *,
    arxiv_30d: int,
    github_repos_7d: int,
    hn_points_7d: int,
    saturation: float,
    velocity: float,
    builder_signal: float,
) -> LifecycleStage:
    """Rule-based lifecycle classification per PLAN.md §6.4.

    Rules evaluated in priority order; first match wins. Defaults to
    'whisper' when no rule matches (semantically: 'we're watching').
    """
    # Commodity — heavy saturation, slow velocity, mature ecosystem
    if saturation > 75 and velocity < 1.1 and github_repos_7d > 100:
        return "commodity"
    # Hype — high saturation AND still accelerating
    if saturation > 60 and velocity > 2.0:
        return "hype"
    # Creator — mid saturation band, sustaining velocity
    if 35 <= saturation <= 60 and velocity > 1.2 and hn_points_7d > 50:
        return "creator"
    # Builder — practitioners shipping repos
    if github_repos_7d >= 3 and saturation < 35 and builder_signal > 0.5:
        return "builder"
    # Whisper — early arxiv signal, few repos, low saturation, real velocity
    if (
        arxiv_30d > 0
        and github_repos_7d < 3
        and saturation < 20
        and velocity > 1.5
    ):
        return "whisper"
    return "whisper"


def tbts(
    *,
    velocity_score: float,
    hidden_gem_score: float,
    lifecycle: LifecycleStage,
    convergence_detected: bool,
) -> int:
    """Composite Trend-Before-Trend Score 0-100 per PLAN.md §6.8."""
    vnorm = min(velocity_score, VELOCITY_CLIP) / VELOCITY_CLIP
    lc_weight = LIFECYCLE_WEIGHTS[lifecycle]
    conv = 1.0 if convergence_detected else 0.0
    raw = (
        TBTS_WEIGHTS["velocity"] * vnorm
        + TBTS_WEIGHTS["hidden_gem"] * hidden_gem_score
        + TBTS_WEIGHTS["lifecycle"] * lc_weight
        + TBTS_WEIGHTS["convergence"] * conv
    )
    return round(raw * 100)


def detect_convergence(
    first_appearances: dict[SourceName, datetime]
) -> ConvergenceEvent:
    """Slide a 72h window across sorted first-appearance timestamps and
    detect any group of ≥3 sources within that window.
    """
    if len(first_appearances) < CONVERGENCE_MIN_SOURCES:
        return ConvergenceEvent(
            detected=False, sources_hit=[], window_hours=0, first_appearance={}
        )

    events = sorted(first_appearances.items(), key=lambda kv: kv[1])
    best_window: Optional[tuple[list[SourceName], dict[SourceName, datetime]]] = None
    best_count = 0

    for i, (_, t_i) in enumerate(events):
        # Extend j while events[j] is within 72h of events[i]
        j = i
        while j < len(events):
            delta_hours = (events[j][1] - t_i).total_seconds() / 3600.0
            if delta_hours > CONVERGENCE_WINDOW_HOURS:
                break
            j += 1
        count = j - i
        if count >= CONVERGENCE_MIN_SOURCES and count > best_count:
            window_sources = [s for s, _ in events[i:j]]
            window_appearances = {s: t for s, t in events[i:j]}
            best_window = (window_sources, window_appearances)
            best_count = count

    if best_window is None:
        return ConvergenceEvent(
            detected=False, sources_hit=[], window_hours=0, first_appearance={}
        )

    sources_hit, appearances = best_window
    return ConvergenceEvent(
        detected=True,
        sources_hit=sources_hit,
        window_hours=CONVERGENCE_WINDOW_HOURS,
        first_appearance=appearances,
    )


def mann_kendall_confidence(daily_series: list[int]) -> float:
    """Signed Mann-Kendall Z-score.

    |Z| > 1.96 → 95% confident there is a monotonic trend (positive Z =
    upward, negative = downward). For series shorter than 4 we return 0.0
    — Mann-Kendall is undefined for tiny samples.
    """
    if len(daily_series) < MANN_KENDALL_MIN_LENGTH:
        return 0.0
    result = mk.original_test(daily_series)
    return float(result.z)
