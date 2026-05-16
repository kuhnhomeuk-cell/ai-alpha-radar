"""TDD for pipeline.cluster — MiniLM + UMAP + HDBSCAN.

The first run downloads MiniLM (~80MB) into ~/.cache/huggingface. Subsequent
test runs use that cache. Tests deliberately use clearly-semantic phrases
(not acronyms) so MiniLM's embedding space cleanly separates them.
"""

import pytest

from pipeline import cluster


def test_empty_input_returns_empty_dict() -> None:
    assert cluster.cluster_terms([]) == {}


def test_single_term_unclustered() -> None:
    # min_cluster_size=2 (v0.1.1) means a single term can't form a cluster.
    out = cluster.cluster_terms(["alpha"])
    assert all(a.cluster_id == -1 for a in out.values())
    assert all(a.cluster_label == "Unclustered Emerging" for a in out.values())


def test_min_cluster_size_is_two_for_topics() -> None:
    """v0.1.1: input is now 30-50 named topics, not hundreds of n-grams; the
    HDBSCAN floor drops from 3 to 2 so smaller semantic groupings can form.
    """
    assert cluster.HDBSCAN_MIN_CLUSTER_SIZE == 2


@pytest.mark.slow
def test_cluster_topics_thin_wrapper_returns_same_shape() -> None:
    """cluster_topics is a semantic alias of cluster_terms for the new pipeline."""
    names = [
        "world model",
        "predictive world model",
        "world simulator",
        "internal world model",
        "supervised fine-tuning",
        "instruction tuning",
        "low-rank adaptation",
        "LoRA fine-tuning",
    ]
    out = cluster.cluster_topics(names)
    assert set(out.keys()) == set(names)
    for ca in out.values():
        assert isinstance(ca.cluster_id, int)
        assert isinstance(ca.cluster_label, str)


@pytest.mark.slow
def test_two_semantic_groups_cluster_separately() -> None:
    # Two clearly distinct semantic clusters. Six terms each, plus one outlier.
    world_model = [
        "world model",
        "predictive world model",
        "world simulator",
        "learned world model",
        "model-based reinforcement learning",
        "internal world model",
    ]
    fine_tuning = [
        "supervised fine-tuning",
        "instruction tuning",
        "low-rank adaptation",
        "LoRA fine-tuning",
        "parameter-efficient fine-tuning",
        "fine-tuning recipes",
    ]
    outlier = ["yogurt parfait"]
    out = cluster.cluster_terms(world_model + fine_tuning + outlier)

    # All world-model terms should share one cluster_id (and not -1)
    wm_ids = {out[t].cluster_id for t in world_model}
    ft_ids = {out[t].cluster_id for t in fine_tuning}
    assert -1 not in wm_ids, f"world-model terms unexpectedly noisy: {wm_ids}"
    assert -1 not in ft_ids, f"fine-tuning terms unexpectedly noisy: {ft_ids}"
    assert len(wm_ids) == 1, f"world-model split across clusters: {wm_ids}"
    assert len(ft_ids) == 1, f"fine-tuning split across clusters: {ft_ids}"
    assert wm_ids != ft_ids, "the two semantic groups collapsed into one cluster"
    # The yogurt outlier should drop to noise
    assert out["yogurt parfait"].cluster_id == -1


@pytest.mark.slow
def test_velocity_picks_cluster_label() -> None:
    # Two padded semantic clusters so HDBSCAN reliably forms them. The
    # velocity-based naming only matters when a cluster exists.
    fine_tuning = [
        "supervised fine-tuning",
        "instruction tuning",
        "low-rank adaptation",
        "LoRA fine-tuning",
        "parameter-efficient fine-tuning",
        "fine-tuning recipes",
        "adapter tuning",
        "prefix tuning",
    ]
    world_model = [
        "world model",
        "predictive world model",
        "world simulator",
        "internal world model",
        "model-based reinforcement learning",
    ]
    velocities = {
        "supervised fine-tuning": 1.0,
        "instruction tuning": 1.0,
        "low-rank adaptation": 1.0,
        "LoRA fine-tuning": 5.0,  # winner in fine-tuning cluster
        "parameter-efficient fine-tuning": 2.0,
        "fine-tuning recipes": 1.0,
        "adapter tuning": 1.0,
        "prefix tuning": 1.0,
        "world model": 1.0,
        "predictive world model": 3.0,
        "world simulator": 7.0,  # winner in world-model cluster
        "internal world model": 1.0,
        "model-based reinforcement learning": 1.0,
    }
    out = cluster.cluster_terms(fine_tuning + world_model, velocities=velocities)
    labels = {a.cluster_label for a in out.values() if a.cluster_id != -1}
    assert "LoRA fine-tuning" in labels, (
        f"expected the highest-velocity term to label its cluster; got {labels}"
    )
    assert "world simulator" in labels, (
        f"expected the highest-velocity term to label its cluster; got {labels}"
    )


@pytest.mark.slow
def test_unclustered_default_label() -> None:
    # Mix of 3 unrelated terms; HDBSCAN should mark most as noise.
    out = cluster.cluster_terms(["yogurt", "asphalt", "kitten meow"])
    for a in out.values():
        if a.cluster_id == -1:
            assert a.cluster_label == "Unclustered Emerging"
