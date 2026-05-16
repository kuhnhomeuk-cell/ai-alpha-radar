"""Test isolation: prevent any live HTTP from the orchestrator path.

`run.main()` auto-fetches every source whose input kwarg is None — that's the
right operator default but the wrong test default. Pytest collection runs
the orchestrator (directly in test_run_and_snapshot, transitively in tests
that probe the pipeline-as-a-whole), and without these monkeypatches each
of huggingface/newsletters/reddit/producthunt/replicate/bluesky fires real
network requests, exhausting retry budgets and hanging the suite.

Tests that genuinely exercise a fetcher's HTTP layer use respx and import
the fetcher module directly — they bypass these stubs.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _stub_live_fetchers(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fetcher-specific HTTP-layer tests (tests/test_fetch_*.py) drive httpx
    # via respx and need the real function objects. Skip stubbing there.
    fname = getattr(request.node, "fspath", None)
    if fname is not None and fname.basename.startswith("test_fetch_"):
        return

    from pipeline.fetch import (
        bluesky,
        huggingface,
        newsletters,
        producthunt,
        reddit,
        replicate,
        semantic_scholar,
    )

    monkeypatch.setattr(huggingface, "fetch_trending_models", lambda *a, **k: [])
    monkeypatch.setattr(newsletters, "fetch_newsletter_signals", lambda *a, **k: [])
    monkeypatch.setattr(reddit, "fetch_top_posts", lambda *a, **k: [])
    monkeypatch.setattr(producthunt, "fetch_trending_launches", lambda *a, **k: [])
    monkeypatch.setattr(replicate, "fetch_trending", lambda *a, **k: [])
    monkeypatch.setattr(bluesky, "read_mention_counts", lambda *a, **k: {})
    monkeypatch.setattr(semantic_scholar, "enrich_papers", lambda *a, **k: {})

    # Novelty embeds + writes data/corpus_centroid_60d.npy as a side-effect.
    # Stub to empty dict so tests don't pollute the repo or pay the MiniLM cost.
    from pipeline import novelty as novelty_mod

    monkeypatch.setattr(
        novelty_mod, "score_topics_against_corpus", lambda topic_canonical_names, **k: {n: 0.0 for n in topic_canonical_names}
    )

    # Redirect persist.update_corpus writes to a tmp dir during tests so the
    # orchestrator's persistence path doesn't write fixture data into the
    # production data/ tree. Fetcher tests that exercise persistence
    # directly already pass an explicit `path=` and bypass the default.
    from pipeline import persist

    monkeypatch.setattr(
        persist, "ROOT_DATA", request.config._tmp_path_factory.mktemp("persist_corpus")
    )
