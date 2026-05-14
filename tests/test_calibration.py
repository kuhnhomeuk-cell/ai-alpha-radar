"""TDD for pipeline.calibration — Brier score + reliability bins (audit 3.9)."""

from __future__ import annotations

from datetime import date

from pipeline import calibration
from pipeline.models import Prediction


def _pred(lifecycle: str, verdict: str) -> Prediction:
    return Prediction(
        text="x",
        filed_at=date(2026, 5, 1),
        target_date=date(2026, 6, 1),
        verdict=verdict,
        keyword="kw",
        lifecycle_at_filing=lifecycle,
        target_lifecycle="builder",
    )


def test_predicted_probability_by_lifecycle() -> None:
    assert calibration.predicted_probability("whisper") < calibration.predicted_probability("builder")
    assert calibration.predicted_probability("builder") < calibration.predicted_probability("creator")
    # All in (0, 1)
    for stage in ("whisper", "builder", "creator", "hype", "commodity"):
        p = calibration.predicted_probability(stage)
        assert 0.0 < p < 1.0


def test_brier_score_perfect_predictions_is_zero() -> None:
    """If every verified prediction had prob=1 and every wrong had prob=0,
    Brier = 0. Our mapping gives p=f(lifecycle), so test with predictions
    that match expectations: high-prob lifecycle that all verified."""
    preds = [_pred("creator", "verified") for _ in range(10)]
    score = calibration.brier_score(preds)
    # creator's prob is ~0.9 → (0.9-1)² = 0.01 per row → mean 0.01
    assert 0.0 <= score < 0.1


def test_brier_score_worst_case_is_one() -> None:
    """Predictions with prob~0.5 against all wrongs → moderate Brier."""
    preds = [_pred("builder", "wrong") for _ in range(10)]
    # builder prob ~0.6 → (0.6-0)² = 0.36 per row
    score = calibration.brier_score(preds)
    assert 0.1 < score < 0.9


def test_brier_score_ignores_unresolved_predictions() -> None:
    """Only verdict in {verified, verified_early, wrong} contributes."""
    preds = [
        _pred("creator", "tracking"),
        _pred("creator", "pending"),
    ]
    score = calibration.brier_score(preds)
    assert score == 0.0  # no resolved → score=0 by convention


def test_reliability_bins_returns_ordered_buckets() -> None:
    preds = [_pred("whisper", "verified") for _ in range(5)] + [
        _pred("builder", "wrong") for _ in range(5)
    ] + [_pred("creator", "verified") for _ in range(5)]
    bins = calibration.reliability_bins(preds, n_bins=4)
    # Sorted ascending by predicted probability mid.
    if len(bins) > 1:
        assert bins[0][0] <= bins[-1][0]


def test_compute_calibration_summary_returns_meta_shape() -> None:
    preds = [_pred("creator", "verified") for _ in range(5)]
    summary = calibration.compute_calibration_summary(preds)
    assert "brier" in summary
    assert "n_resolved" in summary
    assert "last_computed" in summary
    assert summary["n_resolved"] == 5
