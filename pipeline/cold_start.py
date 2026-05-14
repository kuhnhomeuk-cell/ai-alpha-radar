"""Beta-Binomial cold-start smoothing for low-count trend velocities.

Audit item 2.1. Cold-start blindness (audit Theme 2): day-1 of a new trend
produces velocity=0 because there's no history; days where a known trend
has 1-2 mentions get spurious-looking velocity numbers because the raw
ratio mentions_7d / expected_7d is unstable at tiny counts.

Beta-Binomial smoothing: fit an empirical Beta(α, β) prior to the
distribution of historical daily mention counts (normalized). When
today_count < SMOOTHING_THRESHOLD, replace the raw count with the
posterior expectation, which pulls noise toward the historical baseline.

Reference: BayesCNS (https://arxiv.org/html/2410.02126) for the
empirical-Bayes framing on bounded count data.
"""

from __future__ import annotations

from typing import Sequence

# Weak, sparse default prior — used when historical data is insufficient
# for an empirical fit. Mean rate ≈ 1/11 ≈ 9% reflects "most terms get
# few mentions". Tunable as the corpus grows.
DEFAULT_PRIOR: tuple[float, float] = (1.0, 10.0)

SMOOTHING_THRESHOLD = 3  # apply smoothing only when today_count < this


def compute_empirical_prior(
    historical_counts: Sequence[int],
) -> tuple[float, float]:
    """Method-of-moments fit of Beta(α, β) to a sample of historical counts.

    Counts are normalized to rates in [0, 1] by dividing by `max(counts)`.
    Returns DEFAULT_PRIOR when the sample is too small (<2) or the
    variance is degenerate (zero or unbounded).
    """
    if len(historical_counts) < 2:
        return DEFAULT_PRIOR
    max_count = max(historical_counts) or 1
    rates = [c / max_count for c in historical_counts]
    n = len(rates)
    mean = sum(rates) / n
    if mean <= 0 or mean >= 1:
        return DEFAULT_PRIOR
    variance = sum((r - mean) ** 2 for r in rates) / max(n - 1, 1)
    if variance <= 0 or variance >= mean * (1 - mean):
        return DEFAULT_PRIOR
    k = mean * (1 - mean) / variance - 1
    return mean * k, (1 - mean) * k


def smoothed_count(
    today_count: int, alpha: float, beta: float, n_days: int
) -> float:
    """Posterior expected count under Beta(α, β) × Binomial(n_days).

    Equivalent to posterior_mean_rate * n_days. With a sparse prior
    (α << β), low observed counts get pulled down toward the historical
    baseline; high counts dominate the prior and pass through almost
    unchanged.
    """
    rate = (today_count + alpha) / (n_days + alpha + beta)
    return rate * n_days
