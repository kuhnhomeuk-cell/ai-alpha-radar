"""TDD for pipeline.cold_start — Beta-Binomial smoothing (audit 2.1).

The goal: a single-mention term must not produce the same velocity_score
as a 10-mention term just because the math divides by a small expected
window. The empirical prior pulls low-count terms toward the historical
baseline.
"""

from __future__ import annotations

import pytest

from pipeline import cold_start, score


def test_compute_empirical_prior_returns_positive_alpha_beta() -> None:
    counts = [0, 1, 2, 1, 3, 0, 5, 1, 2, 0, 4]
    alpha, beta = cold_start.compute_empirical_prior(counts)
    assert alpha > 0
    assert beta > 0


def test_compute_empirical_prior_falls_back_to_weak_uniform_on_tiny_sample() -> None:
    alpha, beta = cold_start.compute_empirical_prior([])
    assert (alpha, beta) == cold_start.DEFAULT_PRIOR
    alpha2, beta2 = cold_start.compute_empirical_prior([5])
    assert (alpha2, beta2) == cold_start.DEFAULT_PRIOR


def test_smoothed_count_pulls_low_count_toward_prior() -> None:
    """A single mention with a sparse prior should yield a smoothed_count
    well below 1."""
    smoothed = cold_start.smoothed_count(today_count=1, alpha=1.0, beta=10.0, n_days=7)
    assert smoothed < 1.0


def test_smoothed_count_high_count_close_to_raw() -> None:
    """A high observed count dominates the prior."""
    smoothed = cold_start.smoothed_count(today_count=20, alpha=1.0, beta=10.0, n_days=7)
    # Sanity bound — should be on the same order as the raw count, well above 5.
    assert smoothed > 5.0


@pytest.mark.xfail(
    reason="Tests score.velocity's prior_alpha/prior_beta kwargs which land in "
    "Phase 3 (score.py redesign on topics primitive).",
    strict=True,
)
def test_velocity_smoothing_kicks_in_under_threshold() -> None:
    """score.velocity with prior < threshold count → smaller value than raw."""
    raw = score.velocity(1, 30)
    smoothed = score.velocity(1, 30, prior_alpha=1.0, prior_beta=10.0)
    assert smoothed < raw


@pytest.mark.xfail(
    reason="Tests score.velocity's prior_alpha/prior_beta kwargs which land in "
    "Phase 3 (score.py redesign on topics primitive).",
    strict=True,
)
def test_velocity_smoothing_no_effect_above_threshold() -> None:
    """today_count >= 3 means use raw count even with prior."""
    raw = score.velocity(5, 30)
    smoothed = score.velocity(5, 30, prior_alpha=1.0, prior_beta=10.0)
    assert raw == smoothed
