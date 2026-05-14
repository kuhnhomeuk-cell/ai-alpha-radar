"""Per-lifecycle peak_estimate_days range table.

Audit item 2.5. The Claude prompt-C output for `peak_estimate_days` is
clamped to these ranges so a 'whisper'-stage trend can't claim a 200-day
peak horizon. None/None means "no clamp" — commodity stage has reached
peak so this field doesn't apply.

Ranges are inclusive on both bounds.
"""

from __future__ import annotations

from typing import Optional

from pipeline.models import LifecycleStage

HORIZONS: dict[LifecycleStage, tuple[Optional[int], Optional[int]]] = {
    "whisper": (14, 30),
    "builder": (30, 60),
    "creator": (21, 45),
    "hype": (7, 21),
    "commodity": (None, None),
}


def clamp_peak_days(
    days: Optional[int], stage: LifecycleStage
) -> Optional[int]:
    """Return `days` clamped to the inclusive horizon for `stage`.

    - None input → None output (treat as "model declined to estimate").
    - commodity stage → None always (the trend has already peaked).
    - Otherwise: max(lo, min(hi, days)).
    """
    if days is None:
        return None
    lo, hi = HORIZONS.get(stage, (None, None))
    if lo is None or hi is None:
        return None
    return max(lo, min(hi, days))
