"""Reciprocal Rank Fusion across heterogeneous source rankings.

Audit item 3.7. The original composite score (saturation = hand-weighted
blend of per-source percentiles) is brittle when one source goes dark:
its weighted contribution vanishes and the overall ordering shifts in
non-obvious ways. RRF is the industry-standard fix — fuse multiple
rankings into a single rank by summing 1/(k + rank) per source.

Constant k=60 is the canonical setting from Cormack et al. (2009),
robust across most fusion problems.

Scope deviation: the audit doc says "replaces hand-weighted composite".
For minimal disruption (saturation feeds into hidden_gem and lifecycle
classification), this commit adds rrf_score as a new computed value
alongside saturation. A later refactor can swap saturation for the RRF
output once the downstream consumers are ready.
"""

from __future__ import annotations

from typing import Mapping

RRF_K = 60


def ranks_from_counts(
    counts: Mapping[str, int], *, descending: bool = True
) -> dict[str, int]:
    """Convert a {item: count} mapping into {item: rank (1=top)}.

    Zero counts are excluded — they don't contribute to the fusion.
    Ties get the same rank.
    """
    items = [(item, c) for item, c in counts.items() if c > 0]
    items.sort(key=lambda kv: kv[1], reverse=descending)
    ranks: dict[str, int] = {}
    last_value: float | None = None
    last_rank = 0
    for i, (item, value) in enumerate(items, start=1):
        if last_value is not None and value == last_value:
            ranks[item] = last_rank
        else:
            ranks[item] = i
            last_rank = i
        last_value = value
    return ranks


def rrf_score(
    rankings: Mapping[str, Mapping[str, int]], *, k: int = RRF_K
) -> dict[str, float]:
    """Fuse per-source rankings into a single {item: rrf_score} dict.

    `rankings[source][item]` = rank of `item` in `source` (1=top).
    Sums 1/(k+rank) across all sources for each item that appears at
    least once.
    """
    out: dict[str, float] = {}
    for source_ranks in rankings.values():
        for item, rank in source_ranks.items():
            out[item] = out.get(item, 0.0) + 1.0 / (k + rank)
    return out
