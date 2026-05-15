"""Diachronic embedding novelty: how far is today's term from the rolling corpus?

Audit item 3.10. Cosine distance of each term's embedding from a 60-day
rolling corpus centroid — high distance means the term is semantically
out-of-distribution vs. recent activity, a useful "this is new"
heuristic that complements velocity (which only measures count change).

Storage: a single .npy file (data/corpus_centroid_60d.npy) holds the
exponential moving average centroid. Each daily run blends today's
candidate-term centroid into it with α=1/60 (≈60-day half-life).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

DEFAULT_CENTROID_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "corpus_centroid_60d.npy"
)
ROLLING_ALPHA = 1.0 / 60.0  # ~60d EMA


def compute_centroid(embeddings: np.ndarray) -> np.ndarray:
    """Mean across axis 0. Input is (n, d) — 1d for a single embedding fails."""
    arr = np.asarray(embeddings, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0:
        raise ValueError(f"compute_centroid expects (n, d) with n>0; got {arr.shape}")
    return arr.mean(axis=0)


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=float).flatten()
    b = np.asarray(b, dtype=float).flatten()
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


def update_rolling_centroid(
    prior: Optional[np.ndarray], today: np.ndarray, *, alpha: float = ROLLING_ALPHA
) -> np.ndarray:
    """EMA: prior * (1-α) + today * α. None prior → today."""
    today = np.asarray(today, dtype=float)
    if prior is None:
        return today
    return prior * (1.0 - alpha) + today * alpha


def save_centroid(centroid: np.ndarray, path: Path = DEFAULT_CENTROID_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, centroid)


def load_centroid(path: Path = DEFAULT_CENTROID_PATH) -> Optional[np.ndarray]:
    if not path.exists():
        return None
    return np.load(path)


def score_topics_against_corpus(
    topic_canonical_names: list[str],
    *,
    corpus_centroid_path: Path = DEFAULT_CENTROID_PATH,
) -> dict[str, float]:
    """High-level wrapper used by the orchestrator.

    Embeds each topic name once, looks up the persisted 60d rolling
    centroid, returns {topic_name: cosine_distance_from_centroid}, and
    blends today's mean embedding into the centroid for tomorrow.

    Day-1 (no prior centroid): every topic gets distance 0.0 and the
    centroid is seeded from today's embeddings.
    """
    if not topic_canonical_names:
        return {}
    # Lazy import — sentence-transformers is a heavy dep we don't want at
    # module import time (matters for fast unit-test collection).
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    embeddings = np.asarray(
        model.encode(topic_canonical_names, show_progress_bar=False, normalize_embeddings=True)
    )
    prior = load_centroid(corpus_centroid_path)
    out: dict[str, float] = {}
    if prior is None:
        # Day-1: no comparison possible; seed centroid + return zeros.
        for name in topic_canonical_names:
            out[name] = 0.0
    else:
        for i, name in enumerate(topic_canonical_names):
            out[name] = cosine_distance(embeddings[i], prior)
    today_centroid = compute_centroid(embeddings)
    updated = update_rolling_centroid(prior, today_centroid)
    save_centroid(updated, corpus_centroid_path)
    return out
