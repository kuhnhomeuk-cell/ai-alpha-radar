"""Two-state Kleinberg-style burst detector with Poisson likelihood.

Audit item 2.2. Theme 2 (cold-start blindness): velocity_score collapses
near zero on sparse early-window data, so a sustained-elevated trend
looks identical to noise. Kleinberg's burst detection complements
velocity by spotting a contiguous run of unusually-high days even when
the global rate is low.

Model: two-state HMM, state 0 = normal (rate λ0 = mean of series),
state 1 = burst (rate λ1 = BURST_SCALING · λ0). Per-day observation cost
= -log Poisson(count | λ_q) (constants dropped — they cancel in argmin).
Transition cost γ applied when the state flips between adjacent days.
Viterbi finds the cheapest sequence; burst_score returns the fraction
of days assigned to state 1.

Deviation from Kleinberg's original (binomial-over-document-totals): we
use Poisson on counts directly, which fits naturally to per-term daily
mention counts and avoids needing a global denominator series. Surfaced
here for transparency.
"""

from __future__ import annotations

import math
from typing import Sequence

BURST_SCALING = 2.0  # λ1 = BURST_SCALING * λ0
TRANSITION_COST = 1.0


def _poisson_neg_log_likelihood(r: int, lam: float) -> float:
    """-log Poisson(r | λ), dropping the constant log(r!) term.

    Returns 0 when both are zero; a large constant when r > 0 but λ = 0.
    """
    if lam <= 0:
        return 1e9 if r > 0 else 0.0
    return lam - r * math.log(lam)


def kleinberg_states(
    counts: Sequence[int],
    *,
    s: float = BURST_SCALING,
    gamma: float = TRANSITION_COST,
) -> list[int]:
    """Optimal Viterbi state sequence over 0=normal / 1=burst."""
    n = len(counts)
    if n == 0:
        return []
    total = sum(counts)
    if total <= 0:
        return [0] * n
    lam0 = max(total / n, 1e-3)
    lam1 = s * lam0
    if lam1 <= lam0:
        return [0] * n

    # Viterbi DP.
    dp_prev = [
        _poisson_neg_log_likelihood(counts[0], lam0),
        _poisson_neg_log_likelihood(counts[0], lam1) + gamma,
    ]
    backptr: list[list[int]] = [[0, 0]]

    for i in range(1, n):
        new_dp = [0.0, 0.0]
        new_back = [0, 0]
        for q in (0, 1):
            lam = lam0 if q == 0 else lam1
            obs = _poisson_neg_log_likelihood(counts[i], lam)
            from_0 = dp_prev[0] + obs + (gamma if q != 0 else 0.0)
            from_1 = dp_prev[1] + obs + (gamma if q != 1 else 0.0)
            if from_0 <= from_1:
                new_dp[q] = from_0
                new_back[q] = 0
            else:
                new_dp[q] = from_1
                new_back[q] = 1
        dp_prev = new_dp
        backptr.append(new_back)

    states = [0] * n
    states[-1] = 0 if dp_prev[0] <= dp_prev[1] else 1
    for i in range(n - 1, 0, -1):
        states[i - 1] = backptr[i][states[i]]
    return states


def burst_score(counts: Sequence[int]) -> float:
    """Fraction of days the optimal sequence assigns to burst state."""
    if not counts:
        return 0.0
    states = kleinberg_states(counts)
    return sum(states) / len(states)
