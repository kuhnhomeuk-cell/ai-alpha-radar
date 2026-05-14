"""TDD for pipeline.snapshot + pipeline.run end-to-end with fixture inputs.

The orchestrator is exercised with fixtures (no live network, no Claude
calls) and must produce a Snapshot that round-trips through the schema.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline import run, snapshot
from pipeline.fetch import arxiv, github, hackernews
from pipeline.models import Snapshot

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


def test_read_prior_snapshot_returns_none_on_corrupt_json(tmp_path: Path) -> None:
    """Audit 4.4 — a corrupt prior snapshot must not crash the pipeline."""
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "2026-05-01.json").write_text("{ not json", encoding="utf-8")
    assert snapshot.read_prior_snapshot(date(2026, 5, 1), public_dir=tmp_path) is None


def test_read_prior_snapshot_returns_none_on_schema_drift(tmp_path: Path) -> None:
    """Audit 4.4 — a snapshot that fails pydantic validation returns None, not raises."""
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "2026-05-01.json").write_text(
        '{"snapshot_date": "not-a-date"}', encoding="utf-8"
    )
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
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",  # empty
    )
    # Output is a valid Snapshot
    assert isinstance(snap, Snapshot)
    assert snap.snapshot_date == date(2026, 5, 13)
    # Top-N trends produced
    assert 1 <= len(snap.trends) <= run.TOP_N_TRENDS
    # data.json file written and re-parses
    written = (tmp_path / "data.json").read_text(encoding="utf-8")
    re_parsed = Snapshot.model_validate_json(written)
    assert re_parsed.snapshot_date == snap.snapshot_date
    assert len(re_parsed.trends) == len(snap.trends)


def test_orchestrator_meta_records_source_counts(tmp_path: Path) -> None:
    papers, posts, repos = _load_papers(), _load_posts(), _load_repos()
    snap = run.main(
        today=date(2026, 5, 13),
        papers=papers,
        posts=posts,
        repos=repos,
        use_claude=False,
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


def test_run_aborts_when_estimated_cost_exceeds_cap(tmp_path: Path) -> None:
    """When --max-cost-cents is exceeded, refuse to invoke Claude (exit 3)."""
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        run.main(
            today=date(2026, 5, 13),
            papers=_load_papers(),
            posts=_load_posts(),
            repos=_load_repos(),
            use_claude=True,
            max_cost_cents=0.0,  # impossible — any positive estimate exceeds it
            public_dir=tmp_path,
            predictions_log=tmp_path / "predictions.jsonl",
        )
    assert excinfo.value.code == 3
    assert not (tmp_path / "data.json").exists()


def test_run_skips_cost_gate_without_claude(tmp_path: Path) -> None:
    """max_cost_cents has no effect when use_claude is False."""
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        max_cost_cents=0.0,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.trends  # ran to completion


def test_run_aborts_on_multi_source_failure(monkeypatch, tmp_path: Path) -> None:
    """When ≥2 sources fail, the pipeline must exit(2) before writing data.json."""
    import pytest

    from pipeline.fetch import arxiv as arxiv_mod
    from pipeline.fetch import hackernews as hn_mod

    def boom(*args, **kwargs):
        raise RuntimeError("simulated fetch failure")

    monkeypatch.setattr(arxiv_mod, "fetch_recent_papers", boom)
    monkeypatch.setattr(hn_mod, "fetch_ai_posts", boom)
    monkeypatch.delenv("GH_PAT", raising=False)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_KEY", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        run.main(
            today=date(2026, 5, 13),
            use_claude=False,
            public_dir=tmp_path,
            predictions_log=tmp_path / "predictions.jsonl",
        )
    assert excinfo.value.code == 2
    assert not (tmp_path / "data.json").exists()


def test_run_tolerates_single_source_failure(monkeypatch, tmp_path: Path) -> None:
    """Exactly one source down (GH missing, S2 has data) should NOT abort the run."""
    from pipeline.fetch.semantic_scholar import CitationInfo

    monkeypatch.delenv("GH_PAT", raising=False)
    papers = _load_papers()
    snap = run.main(
        today=date(2026, 5, 13),
        papers=papers,
        posts=_load_posts(),
        s2_data={
            papers[0].id: CitationInfo(
                citation_count=10, influential_citation_count=1, references_count=2
            )
        },
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.meta["sources"]["github"]["ok"] is False
    assert (tmp_path / "data.json").exists()


def test_orchestrator_wires_semantic_scholar_when_s2_data_provided(tmp_path: Path) -> None:
    from pipeline.fetch.semantic_scholar import CitationInfo

    papers = _load_papers()
    # Stub S2 citation data for the first 3 fetched papers.
    s2_data = {
        papers[0].id: CitationInfo(
            citation_count=100, influential_citation_count=10, references_count=20
        ),
        papers[1].id: CitationInfo(
            citation_count=50, influential_citation_count=5, references_count=15
        ),
        papers[2].id: CitationInfo(
            citation_count=25, influential_citation_count=2, references_count=10
        ),
    }
    snap = run.main(
        today=date(2026, 5, 13),
        papers=papers,
        posts=_load_posts(),
        repos=_load_repos(),
        s2_data=s2_data,
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.meta["sources"]["semantic_scholar"]["ok"] is True
    assert snap.meta["sources"]["semantic_scholar"]["fetched"] == 3
    # At least one trend should carry a non-zero citation aggregation.
    assert any(t.sources.semantic_scholar_citations_7d > 0 for t in snap.trends)


def test_orchestrator_marks_s2_failure_when_no_data(tmp_path: Path) -> None:
    # Empty s2_data dict (passed explicitly) = S2 reached but no papers indexed.
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        s2_data={},
        use_claude=False,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )
    assert snap.meta["sources"]["semantic_scholar"]["ok"] is False
    assert snap.meta["sources"]["semantic_scholar"]["fetched"] == 0
    for t in snap.trends:
        assert t.sources.semantic_scholar_citations_7d == 0


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
        public_dir=tmp_path,
        predictions_log=log,
    )
    assert snap.hit_rate.verified == 1
    assert snap.hit_rate.rate == 1.0
