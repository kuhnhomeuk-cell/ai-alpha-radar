"""PELT changepoint detection over the daily count sparkline.

Audit item 2.3. velocity_acceleration shipped hardcoded at 0.0 in Wave 1.
PELT (Pruned Exact Linear Time) detects abrupt regime shifts in a
time series; we use the breakpoint location to compute a real
acceleration term:

    velocity_acceleration = (today_count - count_at_last_breakpoint)
                            / max(days_since_breakpoint, 1)

If no breakpoint is detected the acceleration falls back to 0.0 — the
series has no statistically distinguishable regime shift.

Uses ruptures.Pelt with the RBF kernel cost per the audit spec.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import ruptures as rpt

PELT_MODEL = "rbf"
PELT_PENALTY = 3.0


def find_breakpoints(series: Sequence[float]) -> list[int]:
    """Return PELT breakpoint indices for `series` (1-indexed, last index is N)."""
    if len(series) < 2:
        return []
    arr = np.asarray(series, dtype=float).reshape(-1, 1)
    algo = rpt.Pelt(model=PELT_MODEL, min_size=2, jump=1).fit(arr)
    return algo.predict(pen=PELT_PENALTY)


def velocity_acceleration(
    series: Sequence[int], *, today_count: int
) -> float:
    """Acceleration since the most recent breakpoint, signed."""
    if not series:
        return 0.0
    full = list(series) + [today_count]
    bps = find_breakpoints(full)
    # The final entry in `bps` is always len(full); ignore it as a breakpoint.
    inner = [b for b in bps if 0 < b < len(full)]
    if not inner:
        return 0.0
    last_bp = inner[-1]
    count_at_bp = full[last_bp - 1] if last_bp - 1 < len(full) else full[-1]
    days_since = len(full) - last_bp
    if days_since <= 0:
        return 0.0
    return (today_count - count_at_bp) / days_since
