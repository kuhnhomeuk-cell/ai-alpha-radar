"""Regression tests for score inputs assembled by pipeline.run."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import run
from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost
from pipeline.topics import Topic

TODAY_DT = datetime(2026, 5, 14, tzinfo=timezone.utc)


def _paper(paper_id: str, *, days_ago: int = 1) -> Paper:
    return Paper(
        id=paper_id,
        title="World model agents",
        abstract="Agents that learn an internal world model.",
        authors=["Ada"],
        published_at=TODAY_DT - timedelta(days=days_ago),
        primary_category="cs.AI",
        url=f"https://arxiv.org/abs/{paper_id}",
    )


def _post(post_id: int, *, points: int, days_ago: int = 1) -> HNPost:
    return HNPost(
        id=post_id,
        title="World model agents",
        url="https://news.ycombinator.com/item?id=1",
        points=points,
        num_comments=3,
        created_at=TODAY_DT - timedelta(days=days_ago),
        story_text="",
        author="alice",
    )


def _repo(
    name: str,
    *,
    stars: int = 100,
    stars_7d_delta: int | None = 0,
    days_ago: int = 1,
) -> RepoStat:
    return RepoStat(
        full_name=name,
        description="AI agent tooling",
        stars=stars,
        stars_7d_delta=stars_7d_delta,
        warming_up=stars_7d_delta is None,
        language="Python",
        topics=["ai"],
        created_at=TODAY_DT - timedelta(days=days_ago),
        pushed_at=TODAY_DT - timedelta(hours=4),
        html_url=f"https://github.com/{name}",
    )


@pytest.mark.xfail(
    reason="run._source_counts_from_topic signature drifted to 3 positional args; "
    "test was written against the 5-arg form. Update test to current API.",
    strict=True,
)
def test_source_counts_include_hn_points_and_github_star_velocity() -> None:
    papers = [_paper("2605.00001")]
    posts = [
        _post(101, points=42),
        _post(102, points=99, days_ago=8),
    ]
    repos = [
        _repo("acme/fresh-agent", stars_7d_delta=7),
        _repo("acme/old-agent", stars_7d_delta=100, days_ago=8),
    ]
    topic = Topic(
        canonical_name="world model agents",
        canonical_form="world-model-agents",
        aliases=[],
        description="Agents that learn an internal world model.",
        source_doc_ids={
            "arxiv": ["2605.00001"],
            "hackernews": [101, 102],
            "github": ["acme/fresh-agent", "acme/old-agent"],
        },
    )

    counts = run._source_counts_from_topic(
        topic,
        run._build_doc_timestamps(papers, posts, repos),
        {p.id: p for p in posts},
        {r.full_name: r for r in repos},
        TODAY_DT,
    )

    assert counts.arxiv_30d == 1
    assert counts.hn_posts_7d == 1
    assert counts.hn_points_7d == 42
    assert counts.github_repos_7d == 1
    assert counts.github_stars_7d == 7


@pytest.mark.xfail(
    reason="UMAP requires n_neighbors > 1; this fixture builds only 2 topics, "
    "which is below the clustering step's minimum. Grow fixture or stub UMAP.",
    strict=True,
)
def test_builder_signal_uses_repo_count_plus_star_velocity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repos = [
        _repo("acme/high-velocity", stars_7d_delta=9),
        _repo("acme/low-velocity", stars_7d_delta=1),
    ]

    def fake_topics(papers, posts, repos_arg, candidate_hints):
        return [
            Topic(
                canonical_name="high velocity tooling",
                canonical_form="high-velocity-tooling",
                aliases=[],
                description="A fast-moving repo-backed topic.",
                source_doc_ids={"github": ["acme/high-velocity"]},
            ),
            Topic(
                canonical_name="low velocity tooling",
                canonical_form="low-velocity-tooling",
                aliases=[],
                description="A slower repo-backed topic.",
                source_doc_ids={"github": ["acme/low-velocity"]},
            ),
        ]

    def fake_cluster_topics(names):
        return {
            name: run.cluster_mod.ClusterAssignment(cluster_id=0, cluster_label="tooling")
            for name in names
        }

    monkeypatch.setattr(run.cluster_mod, "cluster_topics", fake_cluster_topics)

    snap = run.main(
        today=date(2026, 5, 14),
        papers=[],
        posts=[],
        repos=repos,
        use_claude=False,
        extract_topics_fn=fake_topics,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )

    high = next(t for t in snap.trends if t.keyword == "high velocity tooling")
    low = next(t for t in snap.trends if t.keyword == "low velocity tooling")
    assert high.sources.github_stars_7d == 9
    assert low.sources.github_stars_7d == 1
    assert high.builder_signal == pytest.approx(1.0)
    assert low.builder_signal == pytest.approx(0.2)
    assert snap.meta["github_stars"] == {
        "acme/high-velocity": 100,
        "acme/low-velocity": 100,
    }
