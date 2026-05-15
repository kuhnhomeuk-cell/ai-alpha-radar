"""Meta-Trends: 2nd-pass HDBSCAN over per-cluster centroids.

Audit item 3.13. Surface the parent narrative across clusters — a
"meta-trend" is a group of related clusters that share a higher-level
theme. With ~10 clusters per day, 2-3 meta-trends typically emerge.

Labeling: tiny meta-clusters (2 members) get a deterministic
"{a} + {b}" fallback label. Larger meta-clusters can optionally be
labelled by Claude via summarize.label_meta_trend.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

import hdbscan
import numpy as np

META_MIN_CLUSTER_SIZE = 2


def cluster_centroids(
    centroids: Mapping[int, Sequence[float]],
    *,
    min_cluster_size: int = META_MIN_CLUSTER_SIZE,
) -> dict[int, int]:
    """Return {cluster_id: meta_cluster_id}. -1 indicates noise (no meta)."""
    if not centroids:
        return {}
    ids = list(centroids.keys())
    arr = np.array([list(centroids[i]) for i in ids], dtype=float)
    if arr.shape[0] < min_cluster_size:
        return {i: -1 for i in ids}
    # min_samples=1 + cluster_selection_method='leaf' is required to make
    # HDBSCAN return non-noise labels on the small (~10 centroids) inputs
    # we feed it — the default settings collapse everything to -1.
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        cluster_selection_method="leaf",
        metric="euclidean",
    )
    labels = clusterer.fit_predict(arr)
    return {i: int(lbl) for i, lbl in zip(ids, labels)}


def fallback_label(member_labels: Sequence[str], *, max_members: int = 3) -> str:
    """Cheap label = first N member labels joined by ' + '. No LLM cost."""
    trimmed = [m for m in member_labels if m and m.lower() != "unclustered emerging"]
    if not trimmed:
        return "Unclustered"
    return " + ".join(trimmed[:max_members])


def build_meta_trend_labels(
    cluster_to_meta: Mapping[int, int],
    cluster_labels: Mapping[int, str],
    *,
    claude_labeler: Optional[callable] = None,
    min_members_for_claude: int = 3,
) -> dict[int, str]:
    """Return {meta_cluster_id: label_string}.

    Tiny meta-clusters use the fallback "a + b" label; larger ones
    optionally call into `claude_labeler(member_labels) -> str` for a
    real parent narrative.
    """
    members_by_meta: dict[int, list[str]] = {}
    for cid, meta_id in cluster_to_meta.items():
        if meta_id == -1:
            continue
        members_by_meta.setdefault(meta_id, []).append(cluster_labels.get(cid, str(cid)))

    out: dict[int, str] = {}
    for meta_id, member_labels in members_by_meta.items():
        if claude_labeler is not None and len(member_labels) >= min_members_for_claude:
            try:
                out[meta_id] = claude_labeler(member_labels)
                continue
            except Exception:
                pass
        out[meta_id] = fallback_label(member_labels)
    return out
