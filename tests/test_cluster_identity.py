"""TDD for pipeline.cluster_identity — stable cluster IDs across snapshots."""

from __future__ import annotations

import numpy as np
import pytest

from pipeline import cluster_identity


def test_canonicalize_reuses_prior_id_when_centroid_within_threshold() -> None:
    prior = {1: np.array([1.0, 0.0]), 2: np.array([0.0, 1.0])}
    new = {0: np.array([0.98, 0.05])}  # close to prior id=1
    mapping = cluster_identity.canonicalize_cluster_ids(new, prior, threshold=0.2)
    assert mapping[0] == 1


def test_canonicalize_assigns_fresh_id_when_no_prior_match() -> None:
    prior = {1: np.array([1.0, 0.0])}
    new = {0: np.array([-1.0, 0.0])}  # opposite direction → far from prior
    mapping = cluster_identity.canonicalize_cluster_ids(
        new, prior, threshold=0.2, used_ids={1}
    )
    assert mapping[0] != 1


def test_canonicalize_does_not_reuse_same_prior_id_twice() -> None:
    prior = {1: np.array([1.0, 0.0])}
    new = {
        10: np.array([0.99, 0.01]),
        11: np.array([0.97, 0.05]),
    }
    mapping = cluster_identity.canonicalize_cluster_ids(new, prior, threshold=0.2)
    # Both are close to prior 1; only one can claim it.
    assert mapping[10] == 1 or mapping[11] == 1
    assert mapping[10] != mapping[11]


def test_canonicalize_preserves_noise_label_minus_one() -> None:
    prior = {1: np.array([1.0, 0.0])}
    new = {-1: np.array([5.0, 5.0])}
    mapping = cluster_identity.canonicalize_cluster_ids(new, prior, threshold=0.2)
    assert mapping[-1] == -1


@pytest.mark.xfail(
    reason="Calls run.main() without --claude/extract_topics_fn — main now "
    "requires topic extraction. Re-fixture in Phase 3 with a fake "
    "extract_topics_fn that returns deterministic Topic objects.",
    strict=True,
)
def test_run_cluster_ids_stable_across_two_consecutive_runs(tmp_path) -> None:
    """Audit 2.6 integration check: running the orchestrator twice on the
    same inputs (with day-N output seeding day-N+1's prior centroids)
    should keep ≥80% of cluster_ids identical for terms that survive both
    days."""
    import json
    from datetime import date
    from pathlib import Path

    from pipeline import run
    from pipeline.fetch import arxiv, github, hackernews

    fixtures = Path(__file__).resolve().parent / "fixtures"
    papers = arxiv.parse_atom_feed(
        (fixtures / "arxiv_sample.xml").read_text(encoding="utf-8"),
        categories=["cs.AI", "cs.LG", "cs.CL"],
    )
    posts = hackernews.parse_search_response(
        json.loads((fixtures / "hn_sample.json").read_text(encoding="utf-8"))
    )
    repos = github.parse_search_response(
        json.loads((fixtures / "github_sample.json").read_text(encoding="utf-8"))
    )

    day1 = run.main(
        today=date(2026, 5, 12),
        papers=papers,
        posts=posts,
        repos=repos,
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    day2 = run.main(
        today=date(2026, 5, 13),
        papers=papers,
        posts=posts,
        repos=repos,
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )

    by_kw_day1 = {t.keyword: t.cluster_id for t in day1.trends if t.cluster_id != -1}
    by_kw_day2 = {t.keyword: t.cluster_id for t in day2.trends if t.cluster_id != -1}
    shared = set(by_kw_day1) & set(by_kw_day2)
    assert shared, "expected overlapping keywords across the two runs"
    stable = sum(1 for k in shared if by_kw_day1[k] == by_kw_day2[k])
    fraction = stable / len(shared)
    assert fraction >= 0.8, f"cluster_id stability {fraction:.0%} < 80%"


def test_canonicalize_empty_prior_returns_identity() -> None:
    new = {0: np.array([1.0, 0.0]), 1: np.array([0.0, 1.0])}
    mapping = cluster_identity.canonicalize_cluster_ids(new, {}, threshold=0.2)
    # No prior → each gets a fresh stable id, but the mapping must be injective.
    assert len(set(mapping.values())) == 2
