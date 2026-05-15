"""TDD for pipeline.meta_trends — 2nd-pass HDBSCAN over cluster centroids."""

from __future__ import annotations

import numpy as np

from pipeline import meta_trends


def test_meta_clusters_two_close_clusters_share_a_meta_id() -> None:
    """Two centroids near each other → same meta-cluster id (not -1)."""
    centroids = {
        1: [1.0, 0.0, 0.0],
        2: [0.99, 0.05, 0.0],
        3: [-1.0, 0.0, 0.0],
        4: [-0.95, 0.02, 0.0],
    }
    result = meta_trends.cluster_centroids(centroids, min_cluster_size=2)
    # Clusters 1+2 should share a meta id; 3+4 should share a different meta id.
    assert result[1] == result[2]
    assert result[3] == result[4]
    assert result[1] != result[3]


def test_meta_clusters_isolated_centroid_gets_noise_minus_one() -> None:
    centroids = {
        1: [1.0, 0.0, 0.0],
        2: [0.99, 0.0, 0.0],
        3: [-5.0, -5.0, -5.0],  # far away, will be noise
    }
    result = meta_trends.cluster_centroids(centroids, min_cluster_size=2)
    assert result[3] == -1


def test_meta_clusters_empty_returns_empty() -> None:
    assert meta_trends.cluster_centroids({}, min_cluster_size=2) == {}


def test_fallback_label_joins_member_labels() -> None:
    label = meta_trends.fallback_label(["world models", "browser agents"])
    assert "world models" in label
    assert "browser agents" in label
