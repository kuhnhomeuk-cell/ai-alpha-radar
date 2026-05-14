"""Brier score + reliability decomposition for the prediction log.

Audit item 3.9. Predictions today don't carry an explicit predicted
probability — they carry a `lifecycle_at_filing` that loosely encodes
prior likelihood of reaching the target. We map lifecycle → probability
via LIFECYCLE_PRIOR, then compute Brier across resolved verdicts.

A future iteration that adds a real `predicted_probability` field on
Prediction can drop the LIFECYCLE_PRIOR map and use it directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from pipeline.models import LifecycleStage, Prediction

# Rough prior P(reaches target lifecycle | filed at this stage). Tuned by
# experience, not regression — this is the v1 baseline. Replace with a
# fitted prior once we have ≥100 resolved predictions.
LIFECYCLE_PRIOR: dict[LifecycleStage, float] = {
    "whisper": 0.4,
    "builder": 0.6,
    "creator": 0.85,
    "hype": 0.5,
    "commodity": 0.2,
}

RESOLVED_VERDICTS = frozenset({"verified", "verified_early", "wrong"})


def predicted_probability(lifecycle: LifecycleStage | None) -> float:
    if lifecycle is None:
        return 0.5
    return LIFECYCLE_PRIOR.get(lifecycle, 0.5)


def _is_hit(verdict: str) -> int:
    return 1 if verdict in ("verified", "verified_early") else 0


def brier_score(predictions: Sequence[Prediction]) -> float:
    """Mean (probability - actual)² over resolved predictions. Zero if none."""
    resolved = [p for p in predictions if p.verdict in RESOLVED_VERDICTS]
    if not resolved:
        return 0.0
    total = 0.0
    for p in resolved:
        prob = predicted_probability(p.lifecycle_at_filing)
        actual = _is_hit(p.verdict)
        total += (prob - actual) ** 2
    return total / len(resolved)


def reliability_bins(
    predictions: Sequence[Prediction], *, n_bins: int = 4
) -> list[tuple[float, float, int]]:
    """Return [(predicted_prob_bin_mid, actual_hit_rate, count)] sorted ascending.

    Buckets predictions by predicted probability into `n_bins` equal-width
    bins on [0, 1]; only non-empty bins are returned.
    """
    resolved = [p for p in predictions if p.verdict in RESOLVED_VERDICTS]
    if not resolved:
        return []
    bins: dict[int, list[Prediction]] = {}
    for p in resolved:
        prob = predicted_probability(p.lifecycle_at_filing)
        idx = min(int(prob * n_bins), n_bins - 1)
        bins.setdefault(idx, []).append(p)
    out: list[tuple[float, float, int]] = []
    for idx in sorted(bins):
        bucket = bins[idx]
        bucket_mid = (idx + 0.5) / n_bins
        hit_rate = sum(_is_hit(p.verdict) for p in bucket) / len(bucket)
        out.append((bucket_mid, hit_rate, len(bucket)))
    return out


def compute_calibration_summary(predictions: Sequence[Prediction]) -> dict[str, object]:
    """Snapshot.meta payload — brier, n_resolved, ISO timestamp."""
    resolved = [p for p in predictions if p.verdict in RESOLVED_VERDICTS]
    return {
        "brier": brier_score(predictions),
        "n_resolved": len(resolved),
        "last_computed": datetime.now(tz=timezone.utc).isoformat(),
    }
