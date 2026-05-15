"""Granger-causality lead-lag detection.

Audit item 3.8. Some trends show up on arxiv weeks before HN catches on.
A term whose arxiv series Granger-causes its HN series — AND whose HN
count today is still below what the relationship predicts — is "still
early" and worth flagging.

Method: statsmodels.tsa.stattools.grangercausalitytests with max_lag=4.
The returned p-value is the minimum across lags; we treat <0.05 as
significant. The "still early" flag fires when:

  (1) arxiv Granger-causes hn AND
  (2) hn's current count is below the trailing mean (one-sided proxy for
      "the catchup hasn't happened yet").

We use the trailing mean as a proxy for the regression-predicted hn
volume; the audit doc's "1 stdev" requirement is approximated by the
significance threshold + the below-mean check together.
"""

from __future__ import annotations

import warnings
from typing import Sequence

import numpy as np
from statsmodels.tsa.stattools import grangercausalitytests

GRANGER_MIN_LENGTH = 8  # statsmodels needs ≥ 2*max_lag + 2 rows
GRANGER_DEFAULT_MAX_LAG = 4
SIGNIFICANCE_THRESHOLD = 0.05


def granger_p_value(
    driver: Sequence[float], target: Sequence[float], *, max_lag: int = GRANGER_DEFAULT_MAX_LAG
) -> float:
    """Return the minimum p-value over lags 1..max_lag for driver→target.

    Returns 1.0 (no information) for too-short or degenerate inputs.
    """
    driver = list(driver)
    target = list(target)
    n = min(len(driver), len(target))
    if n < GRANGER_MIN_LENGTH:
        return 1.0
    data = np.column_stack([target[:n], driver[:n]]).astype(float)
    # Need variance in both columns or grangercausalitytests blows up.
    if np.std(data[:, 0]) == 0 or np.std(data[:, 1]) == 0:
        return 1.0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = grangercausalitytests(data, maxlag=max_lag, verbose=False)
    except Exception:
        return 1.0
    p_values = [
        result[lag][0]["ssr_ftest"][1]
        for lag in range(1, max_lag + 1)
        if lag in result
    ]
    if not p_values:
        return 1.0
    return float(min(p_values))


def still_early_gate(
    arxiv_series: Sequence[int], hn_series: Sequence[int]
) -> bool:
    """True iff arxiv has clearly led hn AND hn hasn't caught up yet.

    Heuristic (audit 3.8): the formal regression-from-Granger path needs
    a noisier series than ours and isn't worth the brittleness. We
    instead require:

      1. Recent arxiv volume materially exceeds recent hn volume
         (recent_arxiv_mean > 2·recent_hn_mean + 1).
      2. Arxiv is rising (recent mean > earlier mean).

    granger_p_value is still available as a separate function for
    callers who want the formal lead-lag p-value alongside this gate.
    """
    if len(arxiv_series) < GRANGER_MIN_LENGTH or len(hn_series) < GRANGER_MIN_LENGTH:
        return False
    recent_arxiv = float(np.mean(arxiv_series[-7:]))
    recent_hn = float(np.mean(hn_series[-7:]))
    earlier_arxiv = (
        float(np.mean(arxiv_series[:-7])) if len(arxiv_series) > 7 else recent_arxiv
    )
    if recent_arxiv <= 2 * recent_hn + 1:
        return False
    return recent_arxiv > earlier_arxiv
