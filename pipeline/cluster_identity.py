"""Map fresh HDBSCAN cluster labels onto stable IDs that survive across days.

Audit item 2.6. Theme 4 (cluster IDs reshuffle every day): HDBSCAN's
output labels are 0..k arbitrarily ordered per run, so yesterday's
`cluster_id=3` and today's `cluster_id=3` refer to different groupings.
Watchlists, deep-links, and prediction-tracking depend on stable
identity, so we canonicalize: for each new cluster, compute its centroid
in the reduced-embedding space, find the nearest prior centroid, and
reuse the prior id if cosine distance is below CENTROID_MATCH_THRESHOLD.
Otherwise mint a fresh stable id from `hash(cluster_label)`.

Noise (-1) always maps to -1.
"""

from __future__ import annotations

from typing import Mapping, Optional

import numpy as np

CENTROID_MATCH_THRESHOLD = 0.2  # cosine distance


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float).flatten()
    b = np.asarray(b, dtype=float).flatten()
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - float(np.dot(a, b) / (na * nb))


def _fresh_id(seed_text: str, used: set[int]) -> int:
    """Deterministic positive int not currently in `used`."""
    base = abs(hash(seed_text)) % (2**31 - 1)
    candidate = base or 1
    while candidate in used or candidate < 0:
        candidate = (candidate + 1) % (2**31 - 1)
        if candidate == 0:
            candidate = 1
    return candidate


def canonicalize_cluster_ids(
    new_centroids: Mapping[int, np.ndarray],
    prior_centroids: Mapping[int, np.ndarray],
    *,
    threshold: float = CENTROID_MATCH_THRESHOLD,
    used_ids: Optional[set[int]] = None,
    labels_by_new_id: Optional[Mapping[int, str]] = None,
) -> dict[int, int]:
    """Return {raw_new_cluster_id: canonical_cluster_id}.

    - Noise (-1) always maps to -1.
    - Otherwise: find the nearest prior centroid by cosine distance. If
      within `threshold` AND that prior id isn't already claimed, reuse it.
    - Else: mint a fresh stable int from hash(label or str(raw_id)).
    """
    used: set[int] = set(used_ids or set())
    mapping: dict[int, int] = {}
    if not new_centroids:
        return mapping

    # Sort raw ids deterministically (skip noise; handle separately).
    items = sorted((rid, vec) for rid, vec in new_centroids.items() if rid != -1)
    if -1 in new_centroids:
        mapping[-1] = -1

    available_prior = dict(prior_centroids)
    for raw_id, new_vec in items:
        best_prior_id: Optional[int] = None
        best_dist = float("inf")
        for prior_id, prior_vec in available_prior.items():
            d = _cosine_distance(new_vec, prior_vec)
            if d < best_dist:
                best_dist = d
                best_prior_id = prior_id
        if best_prior_id is not None and best_dist < threshold:
            mapping[raw_id] = best_prior_id
            used.add(best_prior_id)
            del available_prior[best_prior_id]
        else:
            label = (
                labels_by_new_id.get(raw_id, str(raw_id))
                if labels_by_new_id
                else str(raw_id)
            )
            stable = _fresh_id(label + str(raw_id), used)
            mapping[raw_id] = stable
            used.add(stable)
    return mapping
