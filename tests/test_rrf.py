"""TDD for pipeline.rrf — Reciprocal Rank Fusion (audit 3.7)."""

from __future__ import annotations

from pipeline import rrf


def test_rrf_score_higher_for_consistently_top_terms() -> None:
    ranks = {
        "arxiv":      {"a": 1, "b": 2, "c": 3},
        "github":     {"a": 1, "b": 2, "c": 3},
        "hackernews": {"a": 1, "b": 3, "c": 2},
    }
    scores = rrf.rrf_score(ranks)
    # `a` is rank 1 in all three → highest fused score.
    assert scores["a"] > scores["b"]
    assert scores["a"] > scores["c"]


def test_rrf_missing_source_doesnt_break_ordering() -> None:
    """If a source drops out, items still rank by the surviving sources."""
    full = {
        "arxiv": {"a": 1, "b": 2, "c": 3},
        "github": {"a": 2, "b": 1, "c": 3},
        "hackernews": {"a": 3, "b": 2, "c": 1},
    }
    partial = {k: v for k, v in full.items() if k != "hackernews"}
    full_scores = rrf.rrf_score(full)
    partial_scores = rrf.rrf_score(partial)
    # Both runs must produce non-empty results.
    assert set(partial_scores) == {"a", "b", "c"}
    # `a` outranks `c` in arxiv and github → still outranks in partial.
    assert partial_scores["a"] > partial_scores["c"]


def test_rrf_handles_missing_term_in_some_sources() -> None:
    """A term that only appears in one source still scores, just lower."""
    ranks = {
        "arxiv": {"a": 1, "b": 2},
        "github": {"a": 1, "b": 2, "c": 3},
    }
    scores = rrf.rrf_score(ranks)
    assert "c" in scores
    # `c` only ranked once → lowest of the three
    assert scores["c"] < scores["a"]
    assert scores["c"] < scores["b"]


def test_rrf_empty_inputs() -> None:
    assert rrf.rrf_score({}) == {}
    assert rrf.rrf_score({"arxiv": {}}) == {}


def test_ranks_from_counts_assigns_descending_rank_by_value() -> None:
    counts = {"a": 50, "b": 20, "c": 100, "d": 0}
    ranks = rrf.ranks_from_counts(counts)
    assert ranks["c"] == 1  # highest count → rank 1
    assert ranks["a"] == 2
    assert ranks["b"] == 3
    # Zero-count items don't get a rank.
    assert "d" not in ranks
