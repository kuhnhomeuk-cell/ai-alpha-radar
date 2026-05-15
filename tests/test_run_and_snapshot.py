"""TDD for pipeline.snapshot + pipeline.run end-to-end with fixture inputs.

The orchestrator is exercised with fixtures (no live network, no Claude
calls) and must produce a Snapshot that round-trips through the schema.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pipeline import run, snapshot
from pipeline.fetch import arxiv, github, hackernews
from pipeline.models import Snapshot
from pipeline.topics import Topic

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_papers():
    return arxiv.parse_atom_feed(
        (FIXTURES / "arxiv_sample.xml").read_text(encoding="utf-8"),
        categories=["cs.AI", "cs.LG", "cs.CL"],
    )


def _load_posts():
    return hackernews.parse_search_response(
        json.loads((FIXTURES / "hn_sample.json").read_text(encoding="utf-8"))
    )


def _load_repos():
    return github.parse_search_response(
        json.loads((FIXTURES / "github_sample.json").read_text(encoding="utf-8"))
    )


def _stub_extract_topics(papers, posts, repos, candidate_hints):
    """Synthetic topics keyed off the first few fixture docs.

    Tests inject this so the orchestrator can be exercised without a real
    Claude call. Topic names are deliberately distinct so trends[].keyword
    can be asserted directly.
    """
    out: list[Topic] = []
    if papers:
        out.append(Topic(
            canonical_name="world model agents",
            canonical_form="world-model-agents",
            aliases=["WMA"],
            description="Agents that learn an internal world model.",
            source_doc_ids={"arxiv": [papers[0].id]},
        ))
    if posts:
        out.append(Topic(
            canonical_name="llm document corruption",
            canonical_form="llm-document-corruption",
            aliases=[],
            description="When LLMs alter documents during delegation.",
            source_doc_ids={"hackernews": [posts[0].id]},
        ))
    if repos:
        out.append(Topic(
            canonical_name="ai sdk tooling",
            canonical_form="ai-sdk-tooling",
            aliases=[],
            description="New SDKs for building AI applications.",
            source_doc_ids={"github": [repos[0].full_name]},
        ))
    return out


@pytest.fixture(autouse=True)
def _stub_cluster_topics(monkeypatch):
    """Keep orchestrator tests focused on pipeline wiring, not MiniLM/UMAP."""

    def fake_cluster_topics(names):
        return {
            name: run.cluster_mod.ClusterAssignment(
                cluster_id=i,
                cluster_label=f"cluster {i}",
            )
            for i, name in enumerate(names)
        }

    monkeypatch.setattr(run.cluster_mod, "cluster_topics", fake_cluster_topics)


@pytest.fixture(autouse=True)
def _stub_live_fetchers(monkeypatch):
    """Keep orchestrator tests offline: short-circuit HF + Reddit live fetches
    so test_orchestrator_* doesn't hit the network when hf_models / reddit_posts
    aren't explicitly injected.
    """
    monkeypatch.setattr(run.huggingface, "fetch_trending_models", lambda **kw: [])
    monkeypatch.setattr(run.reddit, "fetch_ai_posts", lambda **kw: [])


def test_write_snapshot_creates_data_json_and_dated_archive(tmp_path: Path) -> None:
    snap = Snapshot(
        snapshot_date=date(2026, 5, 13),
        generated_at=run.datetime.now(tz=run.timezone.utc),
        trends=[],
        demand_clusters=[],
        briefing=run._placeholder_briefing(),
        hit_rate=run.predict.compute_hit_rate([]),
        past_predictions=[],
        meta={"trends_processed": 0},
    )
    snapshot.write_snapshot(snap, public_dir=tmp_path)
    assert (tmp_path / "data.json").exists()
    assert (tmp_path / "snapshots" / "2026-05-13.json").exists()


def test_read_prior_snapshot_returns_none_if_missing(tmp_path: Path) -> None:
    assert snapshot.read_prior_snapshot(date(2026, 5, 1), public_dir=tmp_path) is None


def test_read_prior_snapshot_roundtrip(tmp_path: Path) -> None:
    snap = Snapshot(
        snapshot_date=date(2026, 5, 13),
        generated_at=run.datetime.now(tz=run.timezone.utc),
        trends=[],
        demand_clusters=[],
        briefing=run._placeholder_briefing(),
        hit_rate=run.predict.compute_hit_rate([]),
        past_predictions=[],
        meta={"k": "v"},
    )
    snapshot.write_snapshot(snap, public_dir=tmp_path)
    loaded = snapshot.read_prior_snapshot(date(2026, 5, 13), public_dir=tmp_path)
    assert loaded is not None
    assert loaded.snapshot_date == date(2026, 5, 13)
    assert loaded.meta == {"k": "v"}


def test_orchestrator_produces_valid_snapshot_with_fixtures(tmp_path: Path) -> None:
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",  # empty
    )
    # Output is a valid Snapshot
    assert isinstance(snap, Snapshot)
    assert snap.snapshot_date == date(2026, 5, 13)
    # Topic-driven trends produced (stub gives 3)
    assert 1 <= len(snap.trends) <= run.TOP_N_TRENDS
    # data.json file written and re-parses
    written = (tmp_path / "data.json").read_text(encoding="utf-8")
    re_parsed = Snapshot.model_validate_json(written)
    assert re_parsed.snapshot_date == snap.snapshot_date
    assert len(re_parsed.trends) == len(snap.trends)


def test_orchestrator_keyword_values_are_topic_names_not_ngrams(tmp_path: Path) -> None:
    """v0.1.1 regression guard: leaderboard no longer surfaces arxiv-abstract
    verbs. trends[].keyword must equal a Topic.canonical_name.
    """
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    keywords = [t.keyword for t in snap.trends]
    assert "world model agents" in keywords
    # No n-gram noise leaks through
    for bad in ["propose", "framework", "tasks", "experiments", "x27", "x2f", "https"]:
        assert bad not in keywords, f"n-gram noise {bad!r} leaked into trends[].keyword"


def test_orchestrator_propagates_topic_source_doc_ids_to_trends(tmp_path: Path) -> None:
    """v0.1.1: each Trend carries the topic's source_doc_ids for downstream consumers."""
    papers = _load_papers()
    posts = _load_posts()
    snap = run.main(
        today=date(2026, 5, 13),
        papers=papers,
        posts=posts,
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    wma = next(t for t in snap.trends if t.keyword == "world model agents")
    assert wma.source_doc_ids["arxiv"] == [papers[0].id]
    assert wma.aliases == ["WMA"]


def test_orchestrator_meta_records_source_counts(tmp_path: Path) -> None:
    papers, posts, repos = _load_papers(), _load_posts(), _load_repos()
    snap = run.main(
        today=date(2026, 5, 13),
        papers=papers,
        posts=posts,
        repos=repos,
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.meta["sources"]["arxiv"]["fetched"] == len(papers)
    assert snap.meta["sources"]["hackernews"]["fetched"] == len(posts)
    assert snap.meta["sources"]["github"]["fetched"] == len(repos)
    assert snap.meta["trends_processed"] == len(snap.trends)


def test_orchestrator_empty_inputs_writes_empty_snapshot(tmp_path: Path) -> None:
    snap = run.main(
        today=date(2026, 5, 13),
        papers=[],
        posts=[],
        repos=[],
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.trends == []
    assert (tmp_path / "data.json").exists()
    assert snap.meta.get("empty") is True


def test_orchestrator_hard_fails_without_claude_when_inputs_present(tmp_path: Path) -> None:
    """v0.1.1: topic extraction is mandatory when there are source docs.
    No silent placeholder fallback — surface the missing-Claude state loudly.
    """
    with pytest.raises(RuntimeError, match="--claude"):
        run.main(
            today=date(2026, 5, 13),
            papers=_load_papers(),
            posts=_load_posts(),
            repos=_load_repos(),
            use_claude=False,  # and no extract_topics_fn injection
            public_dir=tmp_path,
            predictions_log=tmp_path / "predictions.jsonl",
        )


def test_orchestrator_loads_existing_predictions_into_hit_rate(tmp_path: Path) -> None:
    # Pre-seed a tiny predictions log
    log = tmp_path / "predictions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    from pipeline import predict
    from pipeline.models import Prediction

    predict.append_prediction(
        Prediction(
            keyword="mcp",
            text="x",
            filed_at=date(2026, 5, 1),
            target_date=date(2026, 5, 10),
            verdict="verified",
            lifecycle_at_filing="whisper",
            target_lifecycle="builder",
        ),
        log,
    )
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=log.parent,
        predictions_log=log,
    )
    assert snap.hit_rate.verified == 1
    assert snap.hit_rate.rate == 1.0
