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
