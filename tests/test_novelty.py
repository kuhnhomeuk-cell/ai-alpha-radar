"""TDD for pipeline.novelty — diachronic embedding novelty (audit 3.10)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pipeline import novelty


def test_cosine_distance_zero_for_identical_vectors() -> None:
    v = np.array([1.0, 2.0, 3.0])
    assert novelty.cosine_distance(v, v) < 1e-9


def test_cosine_distance_high_for_orthogonal_vectors() -> None:
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert abs(novelty.cosine_distance(a, b) - 1.0) < 1e-9


def test_compute_centroid_averages_inputs() -> None:
    embeds = np.array([[1.0, 0.0], [3.0, 0.0]])
    c = novelty.compute_centroid(embeds)
    assert np.allclose(c, [2.0, 0.0])


def test_update_rolling_centroid_blends_prior_and_today() -> None:
    prior = np.array([1.0, 0.0])
    today = np.array([0.0, 1.0])
    blended = novelty.update_rolling_centroid(prior, today, alpha=0.5)
    assert np.allclose(blended, [0.5, 0.5])


def test_update_rolling_centroid_no_prior_returns_today() -> None:
    today = np.array([0.5, 0.5])
    out = novelty.update_rolling_centroid(None, today, alpha=0.1)
    assert np.allclose(out, today)


def test_novelty_alien_term_scores_high(tmp_path: Path) -> None:
    """Build a corpus of similar vectors, then assert an alien direction
    scores high novelty (~1.0)."""
    corpus = np.array([[1.0, 0.0, 0.0]] * 50)
    centroid = novelty.compute_centroid(corpus)
    alien = np.array([0.0, 1.0, 0.0])
    familiar = np.array([1.0, 0.05, 0.0])
    assert novelty.cosine_distance(alien, centroid) > novelty.cosine_distance(
        familiar, centroid
    )


def test_save_and_load_centroid_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "corpus.npy"
    c = np.array([0.1, 0.2, 0.3])
    novelty.save_centroid(c, path)
    loaded = novelty.load_centroid(path)
    assert loaded is not None
    assert np.allclose(loaded, c)


def test_load_centroid_missing_returns_none(tmp_path: Path) -> None:
    assert novelty.load_centroid(tmp_path / "nope.npy") is None
