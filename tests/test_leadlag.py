"""TDD for pipeline.leadlag — Granger-based still-early gate (audit 3.8)."""

from __future__ import annotations

from pipeline import leadlag

SIGNIFICANCE_THRESHOLD = 0.05


def test_granger_p_value_lagged_correlation() -> None:
    """A noisy series where target lags driver by 2 should have a low p-value."""
    import numpy as np

    np.random.seed(0)
    driver = list(np.linspace(1, 15, 20) + np.random.normal(0, 0.5, 20))
    target = [0.0, 0.0] + [d + np.random.normal(0, 0.2) for d in driver[:-2]]
    p = leadlag.granger_p_value(driver, target, max_lag=4)
    assert 0.0 <= p <= 1.0
    assert p < SIGNIFICANCE_THRESHOLD


def test_granger_p_value_uncorrelated_returns_high_p() -> None:
    """Constant series → no information → p should not be low."""
    driver = [5] * 20
    target = [3] * 20
    p = leadlag.granger_p_value(driver, target, max_lag=4)
    assert 0.0 <= p <= 1.0


def test_granger_p_value_too_short_returns_one() -> None:
    """Below the min-length cutoff, return 1.0 (no information)."""
    assert leadlag.granger_p_value([1, 2], [1, 2], max_lag=4) == 1.0


def test_still_early_gate_fires_when_arxiv_leads_and_hn_lags() -> None:
    """Synthetic: arxiv steadily up, hn slow to react → still_early."""
    arxiv = [1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    hn = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    assert leadlag.still_early_gate(arxiv, hn) is True


def test_still_early_gate_false_when_hn_already_matches_arxiv() -> None:
    """Arxiv and hn rise together → not still-early."""
    series = list(range(1, 16))
    assert leadlag.still_early_gate(series, series) is False
