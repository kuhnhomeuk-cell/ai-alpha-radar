"""Standalone unit tests for pipeline.lifecycle_horizons.clamp_peak_days.

clamp_peak_days is the spend gate that keeps Claude's peak_estimate_days
output inside the per-lifecycle allowed range.  It has no dedicated test
file — all prior coverage is indirect through enrich_card in test_summarize.
"""

from __future__ import annotations

import pytest

from pipeline import lifecycle_horizons as lh


def test_clamp_peak_days_value_below_floor_raises_to_floor() -> None:
    """A whisper-stage value of 5 days is below the [14, 30] window → clamp to 14."""
    assert lh.clamp_peak_days(5, "whisper") == 14


def test_clamp_peak_days_value_above_ceiling_drops_to_ceiling() -> None:
    """A builder-stage value of 120 days exceeds [30, 60] → clamp to 60."""
    assert lh.clamp_peak_days(120, "builder") == 60


def test_clamp_peak_days_value_in_range_passes_through_unchanged() -> None:
    """A hype-stage value of 14 sits inside [7, 21] → returned unmodified."""
    assert lh.clamp_peak_days(14, "hype") == 14


def test_clamp_peak_days_none_input_returns_none() -> None:
    """Claude declined to estimate → None must pass through, not become a bound."""
    assert lh.clamp_peak_days(None, "builder") is None


def test_clamp_peak_days_commodity_always_returns_none() -> None:
    """Commodity stage has no horizon (already peaked) — any input becomes None."""
    assert lh.clamp_peak_days(10, "commodity") is None
    assert lh.clamp_peak_days(999, "commodity") is None
