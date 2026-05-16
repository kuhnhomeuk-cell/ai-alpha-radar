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


def test_orchestrator_aborts_when_claude_cost_cap_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run.summarize, "estimate_batch_cost_cents", lambda _n: 51.0)

    with pytest.raises(SystemExit) as exc:
        run.main(
            today=date(2026, 5, 13),
            papers=_load_papers(),
            posts=_load_posts(),
            repos=_load_repos(),
            use_claude=True,
            max_cost_cents=50,
            extract_topics_fn=_stub_extract_topics,
            public_dir=tmp_path,
            predictions_log=tmp_path / "predictions.jsonl",
        )
    assert exc.value.code == 3


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


def test_maybe_enrich_predictions_with_claude_replaces_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Claude-generated prediction path should overwrite the
    placeholder prediction on each trend. Per-trend failures should
    leave the placeholder intact so step 10 still appends a row."""
    from pipeline import predict
    from pipeline.models import Prediction

    # Build a minimal Trend list with placeholder predictions
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=Path("/tmp/test_predict_path"),
        predictions_log=Path("/tmp/test_predict_path") / "predictions.jsonl",
    )
    trends_before = list(snap.trends)
    assert all("placeholder" in t.prediction.text for t in trends_before), \
        "fixture sanity: every trend should start with a placeholder"

    # Stub generate_prediction to return a recognisable real prediction.
    # First trend succeeds, second trend raises (degrade-silently path),
    # remaining succeed.
    call_count = {"n": 0}

    def fake_generate(*, keyword, current_lifecycle, today, user_niche, client):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated Sonar outage")
        return Prediction(
            keyword=keyword,
            text=f"Claude says: {keyword} reaches builder by 2026-06-30.",
            filed_at=today,
            target_date=date(2026, 6, 30),
            verdict="pending",
            lifecycle_at_filing=current_lifecycle,
            target_lifecycle="builder",
        )

    monkeypatch.setattr(predict, "generate_prediction", fake_generate)
    # Bypass anthropic.Anthropic() instantiation — fake_generate ignores the client.
    monkeypatch.setattr(run.anthropic, "Anthropic", lambda *_a, **_kw: None)

    enriched, spent = run._maybe_enrich_predictions_with_claude(
        trends_before, today=date(2026, 5, 13), niche="AI tools for solo creators"
    )
    assert len(enriched) == len(trends_before)
    # All but the deliberately-raising second one should have a Claude prediction
    claude_predictions = [t for t in enriched if "Claude says:" in (t.prediction.text or "")]
    placeholders_remaining = [t for t in enriched if "placeholder" in (t.prediction.text or "")]
    assert len(claude_predictions) == len(trends_before) - 1
    assert len(placeholders_remaining) == 1
    # Cost accumulated only for successful calls
    assert spent == pytest.approx(
        run.PREDICTION_COST_CENTS_PER_TREND * len(claude_predictions)
    )


def test_orchestrator_appends_new_predictions_to_log(tmp_path: Path) -> None:
    """append_prediction was defined in pipeline.predict but never invoked
    from the orchestrator — the predictions log stayed frozen at whatever
    a manual session last wrote, so past_predictions was always [].
    Each trend with a non-None prediction (placeholder or Claude-backed)
    should now append a JSONL row, deduplicated on (keyword, target_lifecycle).
    """
    from pipeline import predict

    log = tmp_path / "predictions.jsonl"
    assert not log.exists()
    snap = run.main(
        today=date(2026, 5, 13),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=log,
    )
    assert log.exists(), "predictions.jsonl should be created"
    preds_on_disk = predict.load_predictions(log)
    # Every trend with a prediction surface should appear in the log
    expected = sum(1 for t in snap.trends if t.prediction is not None)
    assert len(preds_on_disk) == expected
    # Re-running with the same fixtures must NOT duplicate rows
    run.main(
        today=date(2026, 5, 14),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=False,
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=log,
    )
    preds_after_second_run = predict.load_predictions(log)
    assert len(preds_after_second_run) == expected


def test_orchestrator_demand_clusters_populate_in_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end verification of the demand-clusters wiring: with use_claude=True
    and the demand mining function stubbed to return canned DemandClusters,
    the resulting snapshot's `demand_clusters` list is populated and survives
    the snapshot writer + Pydantic round-trip.

    This is the integration sibling of tests/test_demand.py — those tests
    exercise demand internals; this test asserts the orchestrator surface
    reads the new function and wires its output into the public artifact.
    """
    from pipeline.models import DemandCluster, DemandQuote

    canned = [
        DemandCluster(
            question_shape="How do solo creators run Claude Desktop locally?",
            askers_estimate=7,
            quotes=[
                DemandQuote(text="How do I run Claude locally?", source="HN"),
                DemandQuote(text="Is there a self-hosted option?", source="HN"),
            ],
            sources=["hackernews"],
            weekly_growth_pct=18.0,
            open_window_days=10,
            creator_brief="Tutorial: Claude Desktop local config for indie creators.",
            related_trends=["claude"],
        ),
        DemandCluster(
            question_shape="What's the best AI agent loop for solo Shorts creators?",
            askers_estimate=5,
            quotes=[DemandQuote(text="Looking for an iterative agent.", source="HN")],
            sources=["hackernews"],
            weekly_growth_pct=14.0,
            open_window_days=21,
            creator_brief="Agent-loop pattern for one-person YouTube Shorts pipelines.",
            related_trends=["agents"],
        ),
    ]

    captured_kwargs: dict = {}

    def fake_mine(*args, **kwargs):
        # Capture for assertions; return canned list.
        captured_kwargs.update(kwargs)
        return list(canned)

    # Patch the demand mine entry on the same module the orchestrator imports.
    monkeypatch.setattr(
        run.demand_mod, "mine_demand_clusters_from_comments", fake_mine
    )
    # The orchestrator also calls Claude card enrichment and the daily
    # briefing under use_claude=True; stub both to skip live API calls.
    monkeypatch.setattr(run, "_maybe_enrich_with_claude", lambda trends, *, niche: trends)
    monkeypatch.setattr(
        run,
        "_maybe_enrich_with_perplexity",
        lambda trends, *, budget_cents: (trends, 0.0),
    )
    from pipeline.models import DailyBriefing
    from datetime import datetime as _dt, timezone as _tz

    monkeypatch.setattr(
        run.summarize,
        "daily_briefing",
        lambda movers, *, niche: DailyBriefing(
            text="stubbed briefing for orchestrator test",
            moved_up=[],
            moved_down=[],
            emerging=[],
            generated_at=_dt.now(tz=_tz.utc),
        ),
    )

    snap = run.main(
        today=date(2026, 5, 16),
        papers=_load_papers(),
        posts=_load_posts(),
        repos=_load_repos(),
        use_claude=True,
        max_cost_cents=1000,  # generous cap so the wiring test isn't budget-gated
        extract_topics_fn=_stub_extract_topics,
        public_dir=tmp_path,
        predictions_log=tmp_path / "predictions.jsonl",
    )

    # Wiring: fake_mine was called with the niche default and fallback kws.
    assert "niche" in captured_kwargs
    assert "fallback_trend_keywords" in captured_kwargs
    assert isinstance(captured_kwargs["fallback_trend_keywords"], list)

    # Output: the snapshot's demand_clusters reflect the canned list.
    assert len(snap.demand_clusters) == 2
    shapes = [c.question_shape for c in snap.demand_clusters]
    assert any("Claude Desktop" in s for s in shapes)
    assert any("AI agent loop" in s for s in shapes)

    # And the written public/data.json round-trips through Snapshot.
    parsed = Snapshot.model_validate_json(
        (tmp_path / "data.json").read_text(encoding="utf-8")
    )
    assert len(parsed.demand_clusters) == 2
