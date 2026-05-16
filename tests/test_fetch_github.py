"""TDD for pipeline.fetch.github against a cached Search API fixture."""

import json
from pathlib import Path

from pipeline.fetch import github

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "github_sample.json"


def _parsed() -> list[github.RepoStat]:
    return github.parse_search_response(json.loads(FIXTURE.read_text()))


def test_parse_search_response_yields_at_least_twenty_repos() -> None:
    repos = _parsed()
    assert len(repos) >= 20


def test_repos_have_required_fields() -> None:
    for r in _parsed():
        assert "/" in r.full_name, f"bad full_name: {r.full_name}"
        assert isinstance(r.stars, int) and r.stars >= 0
        assert isinstance(r.topics, list)
        assert r.html_url.startswith("http")
        assert r.created_at.tzinfo is not None
        assert r.pushed_at.tzinfo is not None


def test_warming_up_default_true_with_null_delta() -> None:
    for r in _parsed():
        assert r.warming_up is True
        assert r.stars_7d_delta is None


def test_compute_star_velocity_with_no_prior_keeps_warming_up() -> None:
    today = _parsed()
    annotated = github.compute_star_velocity(today, prior_stars={})
    assert all(r.warming_up is True and r.stars_7d_delta is None for r in annotated)


def test_compute_star_velocity_with_prior_computes_delta() -> None:
    today = _parsed()
    prior = {r.full_name: max(r.stars - 7, 0) for r in today[:5]}  # +7 stars over 7d
    annotated = github.compute_star_velocity(today, prior_stars=prior)
    matched = [r for r in annotated if r.full_name in prior]
    assert len(matched) == 5
    for r in matched:
        assert r.warming_up is False
        assert r.stars_7d_delta == 7 or r.stars_7d_delta == r.stars  # ==stars if floor at 0 hit
    # repos not in prior stay warming_up
    unmatched = [r for r in annotated if r.full_name not in prior]
    assert all(r.warming_up is True and r.stars_7d_delta is None for r in unmatched)


def test_topics_list_has_ai_or_llm_or_agents() -> None:
    repos = _parsed()
    ai_relevant = {"ai", "llm", "agents", "artificial-intelligence", "agent", "llms"}
    hits = sum(1 for r in repos if any(t in ai_relevant for t in r.topics))
    assert hits >= 5, f"expected ≥5 repos with an AI topic; got {hits}"


def test_gh_topics_includes_expanded_set() -> None:
    """v0.2.0 — topic set expanded beyond the original ai/llm/agents trio
    to cover tool-category labels (rag/mcp/embedding/fine-tuning/transformer)
    so TRENDING REPOS catches AlphaSignal-style tooling signal.
    """
    required = {"ai", "llm", "agents", "rag", "transformer", "mcp", "embedding", "fine-tuning"}
    assert required.issubset(set(github.GH_TOPICS)), (
        f"missing topics: {required - set(github.GH_TOPICS)}"
    )
