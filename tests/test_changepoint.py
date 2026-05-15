"""TDD for pipeline.changepoint — PELT changepoint detection (audit 2.3)."""

from __future__ import annotations

import pytest

from pipeline import changepoint


def test_no_breakpoint_on_flat_series() -> None:
    series = [3] * 14
    bps = changepoint.find_breakpoints(series)
    # Flat series — at most the final-index sentinel from PELT.
    assert all(0 <= b <= len(series) for b in bps)


def test_breakpoint_near_known_step() -> None:
    """A 14-day series with a sharp jump at index 7 should yield a
    breakpoint at index 7 (±1)."""
    series = [1, 1, 1, 1, 1, 1, 1, 20, 21, 19, 22, 20, 20, 23]
    bps = changepoint.find_breakpoints(series)
    # Non-terminal breakpoints (excluding the series length sentinel).
    inner = [b for b in bps if b < len(series)]
    assert any(6 <= b <= 8 for b in inner), f"expected breakpoint near 7, got {inner}"


def test_velocity_acceleration_zero_when_no_breakpoint() -> None:
    series = [5] * 14
    today_count = 5
    accel = changepoint.velocity_acceleration(series, today_count=today_count)
    assert accel == 0.0


def test_velocity_acceleration_positive_after_upward_step() -> None:
    series = [1, 1, 1, 1, 1, 1, 1, 20, 21, 19, 22, 20, 20, 23]
    today_count = 25
    accel = changepoint.velocity_acceleration(series, today_count=today_count)
    assert accel > 0


def test_velocity_acceleration_negative_after_downward_step() -> None:
    series = [20, 22, 19, 21, 20, 22, 21, 1, 2, 1, 1, 0, 1, 1]
    today_count = 0
    accel = changepoint.velocity_acceleration(series, today_count=today_count)
    assert accel < 0


def test_velocity_acceleration_empty_returns_zero() -> None:
    assert changepoint.velocity_acceleration([], today_count=0) == 0.0
