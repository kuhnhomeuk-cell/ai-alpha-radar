"""TDD for pipeline.burst — Kleinberg-style two-state burst detector (audit 2.2).

The detector must:
- Return state 0 for a flat/quiet series
- Return state 1 for sustained-elevated periods
- Treat an isolated single-day spike as not-a-burst (or low-fraction burst)
"""

from __future__ import annotations

import pytest

from pipeline import burst


def test_burst_empty_returns_zero_score() -> None:
    assert burst.burst_score([]) == 0.0


def test_burst_all_zeros_returns_zero_score() -> None:
    assert burst.burst_score([0] * 14) == 0.0


def test_burst_flat_low_returns_zero_score() -> None:
    """A consistent low-count series is not a burst."""
    assert burst.burst_score([1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 1, 2, 1, 1]) == 0.0


def test_burst_step_function_detects_burst_in_second_half() -> None:
    """Low for 7 days, then high for 7 days → score > 0 (burst captured)."""
    counts = [1, 1, 1, 1, 1, 1, 1] + [20, 25, 22, 30, 28, 24, 26]
    score = burst.burst_score(counts)
    assert score > 0.2


def test_burst_isolated_spike_score_low() -> None:
    """A single very high day amid low days isn't a sustained burst."""
    counts = [1, 1, 1, 1, 1, 1, 1, 50, 1, 1, 1, 1, 1, 1]
    score = burst.burst_score(counts)
    # Should not equal the step-function case; specifically, a single spike
    # gets at most 1/14 of days in burst state.
    assert score <= 1 / 14 + 1e-9


def test_kleinberg_states_returns_state_per_day() -> None:
    states = burst.kleinberg_states([1, 1, 1, 20, 25, 22])
    assert len(states) == 6
    assert all(s in (0, 1) for s in states)
