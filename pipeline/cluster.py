"""Cluster candidate terms into themes via MiniLM + UMAP + HDBSCAN.

Per BACKEND_BUILD §7 Step 8 + PLAN.md §6.7.

Pipeline:
1. Embed each term with sentence-transformers/all-MiniLM-L6-v2 (384-D, 80MB).
2. UMAP reduce to 10-D (cosine, n_neighbors=15, min_dist=0).
3. HDBSCAN (min_cluster_size=3, euclidean).
4. Label each cluster with its highest-velocity member; unclustered (-1)
   gets the literal "Unclustered Emerging" label.

The model lazy-loads on first use and is cached at module level so a single
process doesn't reload it per call. The on-disk cache lives at
~/.cache/huggingface and is reused across runs.
"""

from __future__ import annotations

from typing import Optional

import hdbscan
import umap
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

UMAP_N_COMPONENTS = 10
UMAP_N_NEIGHBORS = 15
UMAP_MIN_DIST = 0.0
UMAP_METRIC = "cosine"

HDBSCAN_MIN_CLUSTER_SIZE = 3
HDBSCAN_METRIC = "euclidean"

UNCLUSTERED_LABEL = "Unclustered Emerging"

_MODEL: Optional[SentenceTransformer] = None


class ClusterAssignment(BaseModel):
    cluster_id: int  # -1 = unclustered
    cluster_label: str


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(MODEL_NAME)
    return _MODEL


def cluster_terms(
    terms: list[str],
    *,
    velocities: Optional[dict[str, float]] = None,
    random_state: int = 42,
) -> dict[str, ClusterAssignment]:
    """Embed → UMAP → HDBSCAN. Returns {term: ClusterAssignment}.

    With fewer terms than HDBSCAN_MIN_CLUSTER_SIZE every term goes to noise
    (cluster_id=-1) — HDBSCAN can't form a cluster smaller than its floor.
    """
    if not terms:
        return {}
    if len(terms) < HDBSCAN_MIN_CLUSTER_SIZE:
        return {
            t: ClusterAssignment(cluster_id=-1, cluster_label=UNCLUSTERED_LABEL)
            for t in terms
        }

    model = _get_model()
    embeddings = model.encode(terms, show_progress_bar=False)

    n_neighbors = min(UMAP_N_NEIGHBORS, len(terms) - 1)
    # Spectral init needs k < N for its sparse eigendecomposition. For tiny
    # term lists fall back to random init — same UMAP geometry, just a
    # different starting point.
    init = "spectral" if len(terms) > UMAP_N_NEIGHBORS else "random"
    reducer = umap.UMAP(
        n_components=min(UMAP_N_COMPONENTS, len(terms) - 1),
        n_neighbors=n_neighbors,
        min_dist=UMAP_MIN_DIST,
        metric=UMAP_METRIC,
        init=init,
        random_state=random_state,
    )
    reduced = reducer.fit_transform(embeddings)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
        metric=HDBSCAN_METRIC,
    )
    labels = clusterer.fit_predict(reduced)

    cluster_to_terms: dict[int, list[str]] = {}
    for term, lbl in zip(terms, labels):
        cluster_to_terms.setdefault(int(lbl), []).append(term)

    cluster_labels: dict[int, str] = {}
    for cluster_id, members in cluster_to_terms.items():
        if cluster_id == -1:
            cluster_labels[cluster_id] = UNCLUSTERED_LABEL
            continue
        if velocities:
            label_term = max(members, key=lambda t: velocities.get(t, 0.0))
        else:
            label_term = sorted(members)[0]
        cluster_labels[cluster_id] = label_term

    return {
        term: ClusterAssignment(
            cluster_id=int(lbl), cluster_label=cluster_labels[int(lbl)]
        )
        for term, lbl in zip(terms, labels)
    }
