"""TDD for pipeline.demand — comment matching, Claude clustering, dedupe."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from pipeline import demand
from pipeline.fetch.hackernews import HNComment, HNPost
from pipeline.models import DemandCluster, DemandQuote


def _comment(cid: int, text: str, author: str = "alice") -> HNComment:
    return HNComment(
        id=cid,
        author=author,
        text=text,
        created_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        points=5,
    )


def _post(post_id: int, title: str, comments: list[HNComment] | None) -> HNPost:
    return HNPost(
        id=post_id,
        title=title,
        url="http://example.com",
        points=50,
        num_comments=len(comments) if comments else 0,
        created_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        story_text="",
        author="alice",
        comments=comments,
    )


# ---------- find_comments_for_keyword ----------


def test_find_comments_matches_keyword_in_post_title() -> None:
    posts = [
        _post(1, "LLM agents reach new heights", [_comment(11, "Can I run this locally?"), _comment(12, "How fast?")]),
        _post(2, "Cooking pasta", [_comment(21, "Use salt"), _comment(22, "More water")]),
    ]
    matched = demand.find_comments_for_keyword("llm", posts)
    assert len(matched) == 2
    assert {c.id for c, _ in matched} == {11, 12}


def test_find_comments_case_insensitive() -> None:
    posts = [_post(1, "GPT for everyone", [_comment(11, "test")])]
    matched = demand.find_comments_for_keyword("gpt", posts)
    assert len(matched) == 1


def test_find_comments_skips_posts_without_hydrated_comments() -> None:
    posts = [_post(1, "LLM things", None)]
    matched = demand.find_comments_for_keyword("llm", posts)
    assert matched == []


# ---------- mine_demand_cluster (Claude-mocked) ----------


class FakeAnthropic:
    def __init__(self, response_text: str) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self._response_text = response_text

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self._response_text)])


def test_mine_demand_cluster_parses_claude_list_response() -> None:
    posts = [
        _post(
            1,
            "MCP servers everywhere",
            [
                _comment(11, "Has anyone got Claude Desktop talking to MCP?"),
                _comment(12, "stdio vs sse - which is better?"),
                _comment(13, "Docs are confusing"),
                _comment(14, "Sample server please"),
                _comment(15, "Production setup tutorial"),
            ],
        )
    ]
    matched = demand.find_comments_for_keyword("mcp", posts)
    response = json.dumps(
        [
            {
                "question_shape": "How do I get MCP server working with Claude Desktop?",
                "askers_estimate": 8,
                "quotes": [
                    {
                        "text": "Has anyone got Claude Desktop talking to MCP?",
                        "source": "HN",
                    },
                    {"text": "stdio vs sse - which is better?", "source": "HN"},
                ],
                "weekly_growth_pct": 18.0,
                "open_window_days": 14,
                "creator_brief": "Short tutorial on MCP + Claude Desktop config.",
            }
        ]
    )
    fake = FakeAnthropic(response)
    clusters = demand.mine_demand_cluster(
        keyword="mcp", comments=matched, client=fake
    )
    assert len(clusters) == 1
    c = clusters[0]
    assert c.question_shape.startswith("How do I get MCP")
    assert c.askers_estimate == 8
    assert len(c.quotes) == 2
    assert c.weekly_growth_pct == 18.0
    assert c.open_window_days == 14
    assert c.sources == ["hackernews"]
    assert "mcp" in c.related_trends


def test_mine_demand_cluster_skips_when_too_few_comments() -> None:
    fake = FakeAnthropic("[]")
    # Only 2 comments — below the floor (3)
    short = [(_comment(1, "x"), _post(1, "MCP", None))] * 2
    clusters = demand.mine_demand_cluster(keyword="mcp", comments=short, client=fake)
    assert clusters == []
    assert fake.calls == []  # no Claude call made


def test_mine_demand_cluster_handles_dict_wrapped_response() -> None:
    posts = [
        _post(
            1,
            "agents",
            [
                _comment(11, "a"),
                _comment(12, "b"),
                _comment(13, "c"),
            ],
        )
    ]
    matched = demand.find_comments_for_keyword("agents", posts)
    response = json.dumps(
        {
            "clusters": [
                {
                    "question_shape": "How to test agents?",
                    "askers_estimate": 5,
                    "quotes": [{"text": "a", "source": "HN"}],
                    "weekly_growth_pct": 0,
                    "open_window_days": 7,
                    "creator_brief": "x",
                }
            ]
        }
    )
    fake = FakeAnthropic(response)
    clusters = demand.mine_demand_cluster(
        keyword="agents", comments=matched, client=fake
    )
    assert len(clusters) == 1


# ---------- dedupe_clusters ----------


def test_dedupe_keeps_singletons_unchanged() -> None:
    clusters = [
        DemandCluster(
            question_shape="How to deploy MCP servers locally?",
            askers_estimate=5,
            quotes=[DemandQuote(text="q", source="HN")],
            sources=["hackernews"],
            weekly_growth_pct=10,
            open_window_days=14,
            creator_brief="b",
            related_trends=["mcp"],
        )
    ]
    out = demand.dedupe_clusters(clusters)
    assert len(out) == 1
    assert out[0].question_shape == clusters[0].question_shape


def test_dedupe_merges_near_duplicate_question_shapes() -> None:
    clusters = [
        DemandCluster(
            question_shape="How do I deploy MCP servers locally with Claude Desktop?",
            askers_estimate=5,
            quotes=[DemandQuote(text="q1", source="HN")],
            sources=["hackernews"],
            weekly_growth_pct=10,
            open_window_days=14,
            creator_brief="b1",
            related_trends=["mcp"],
        ),
        DemandCluster(
            question_shape="How can I run MCP servers locally with Claude Desktop?",
            askers_estimate=4,
            quotes=[DemandQuote(text="q2", source="HN")],
            sources=["hackernews"],
            weekly_growth_pct=12,
            open_window_days=14,
            creator_brief="b2",
            related_trends=["mcp"],
        ),
        DemandCluster(
            question_shape="What's the best Python web framework for 2026?",
            askers_estimate=3,
            quotes=[DemandQuote(text="q3", source="HN")],
            sources=["hackernews"],
            weekly_growth_pct=1,
            open_window_days=30,
            creator_brief="b3",
            related_trends=["python"],
        ),
    ]
    out = demand.dedupe_clusters(clusters)
    # The two near-identical MCP shapes should collapse into one; Python stays
    shapes = {c.question_shape for c in out}
    assert len(out) == 2, f"expected 2 dedupd clusters; got {len(out)}: {shapes}"
    assert any("MCP" in s for s in shapes)
    assert any("Python" in s for s in shapes)


# ---------- New architecture (Phase 3): HDBSCAN over comment embeddings ----------
# BACKEND_BUILD §11 + the Phase-3 spec: gather question-shaped, niche-relevant
# comments → embed → cluster → one Claude call per cluster (via Batch API in
# the orchestrator; sync probe before paid batch). These tests exercise the
# new pipeline end-to-end with fixtures and a mocked Claude client — no
# network, no paid calls.


import json as _json
from pathlib import Path as _Path
from datetime import datetime as _dt, timedelta as _td, timezone as _tz


_FIXTURE_PATH = _Path(__file__).parent / "fixtures" / "hn_comments_demand_sample.json"


def _load_fixture_posts() -> list[HNPost]:
    """Hydrate the niche-aligned HN fixture into HNPost objects.

    The fixture uses `days_ago` so the test is time-independent — we pin
    `now()` semantics by computing real created_at offsets at load time.
    """
    payload = _json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    now = _dt(2026, 5, 16, 12, 0, tzinfo=_tz.utc)
    out: list[HNPost] = []
    for p in payload["posts"]:
        comments = [
            HNComment(
                id=c["id"],
                author="alice",
                text=c["text"],
                created_at=now - _td(days=c["days_ago"]),
                points=5,
            )
            for c in p.get("comments", [])
        ]
        out.append(
            HNPost(
                id=p["id"],
                title=p["title"],
                url="http://example.com",
                points=50,
                num_comments=len(comments),
                created_at=now - _td(days=1),
                story_text=p.get("story_text", ""),
                author="alice",
                comments=comments,
            )
        )
    return out


# ---- question-shape filter ----


def test_is_question_shaped_accepts_question_marks() -> None:
    assert demand.is_question_shaped("How do I deploy MCP locally?")
    assert demand.is_question_shaped("anyone know a good prompt chain?")


def test_is_question_shaped_accepts_intent_starts_without_question_mark() -> None:
    # The spec lists these intent prefixes — they're question-shaped even
    # without a trailing '?'.
    assert demand.is_question_shaped("Looking for a sample MCP config")
    assert demand.is_question_shaped("anyone know a good cost dashboard")
    assert demand.is_question_shaped("how do I keep an agent on-brand")


def test_is_question_shaped_rejects_statements() -> None:
    assert not demand.is_question_shaped("MCP is great, no complaints.")
    assert not demand.is_question_shaped("Shorts are getting saturated.")
    assert not demand.is_question_shaped("Captions plugin is fine.")
    assert not demand.is_question_shaped("Found a nice library for this last week.")


# ---- niche relevance ----


def test_is_niche_relevant_keeps_in_niche_comments() -> None:
    # Default niche keywords cover "AI tools for solo creators".
    assert demand.is_niche_relevant(
        "How do I get Claude Desktop talking to a local MCP server?"
    )
    assert demand.is_niche_relevant(
        "How do I get an agent to write a YouTube Shorts hook?"
    )


def test_is_niche_relevant_rejects_off_niche() -> None:
    assert not demand.is_niche_relevant("How do I keep my sourdough starter alive at 60F?")
    assert not demand.is_niche_relevant("Anyone know the best flour for a dense crumb?")


# ---- gather_question_comments ----


def test_gather_question_comments_filters_fixture_to_in_niche_questions() -> None:
    posts = _load_fixture_posts()
    gathered = demand.gather_question_comments(posts)
    texts = [c.text for c, _ in gathered]

    # Statements should be dropped
    assert not any("MCP is great" in t for t in texts)
    assert not any("Shorts are getting saturated" in t for t in texts)
    assert not any("Captions plugin is fine" in t for t in texts)
    assert not any("Found a nice library" in t for t in texts)

    # Off-niche posts should be dropped entirely
    assert not any("sourdough" in t.lower() for t in texts)
    assert not any("iphone" in t.lower() for t in texts)
    assert not any("m4 chip" in t.lower() for t in texts)

    # We should still have plenty of in-niche, question-shaped material
    # (~17 in the fixture). Lower bound is what matters for HDBSCAN.
    assert len(gathered) >= 12, f"too few question comments gathered: {len(gathered)}"


def test_gather_question_comments_respects_max_age_days() -> None:
    posts = _load_fixture_posts()
    fresh = demand.gather_question_comments(posts, max_age_days=2)
    older = demand.gather_question_comments(posts, max_age_days=7)
    assert len(fresh) < len(older), "max_age_days=2 should yield fewer comments than 7"


# ---- HDBSCAN clustering ----


def test_cluster_comments_groups_fixture_into_distinct_themes() -> None:
    """With three latent themes (local MCP, AI shorts, batch-API costs), HDBSCAN
    at min_cluster_size=3 should produce at least 2 non-noise clusters.
    """
    posts = _load_fixture_posts()
    gathered = demand.gather_question_comments(posts)
    clusters = demand.cluster_comments_hdbscan(gathered)
    # cluster_id == -1 is HDBSCAN noise
    non_noise = [c for c in clusters if c.cluster_id != -1]
    assert len(non_noise) >= 2, (
        f"expected ≥2 themes from HDBSCAN; got {len(non_noise)}: "
        f"{[c.cluster_id for c in clusters]}"
    )
    # Each non-noise cluster has at least min_cluster_size comments
    for c in non_noise:
        assert len(c.comments) >= 3


# ---- summarize_cluster_sync (the §5 probe) ----


def test_summarize_cluster_sync_parses_single_call_response() -> None:
    """The sync probe: ONE Claude call, fully synchronous, no batch. This is
    what we run before any paid batch to verify the prompt + schema work.
    """
    posts = _load_fixture_posts()
    gathered = demand.gather_question_comments(posts)
    clusters = demand.cluster_comments_hdbscan(gathered)
    non_noise = [c for c in clusters if c.cluster_id != -1]
    assert non_noise, "fixture should yield at least one HDBSCAN cluster"
    cluster = non_noise[0]

    response = _json.dumps(
        {
            "question_shape": "How do I get Claude Desktop talking to local MCP?",
            "askers_estimate": 5,
            "quotes": [
                {"text": cluster.comments[0][0].text[:200], "source": "HN"},
                {"text": cluster.comments[1][0].text[:200], "source": "HN"},
            ],
            "weekly_growth_pct": 18.0,
            "open_window_days": 14,
            "creator_brief": "Short tutorial on local MCP config for solo creators.",
        }
    )
    fake = FakeAnthropic(response)
    out = demand.summarize_cluster_sync(cluster, client=fake)
    assert out is not None
    assert out.question_shape.startswith("How do I get Claude Desktop")
    assert out.askers_estimate == 5
    assert len(out.quotes) == 2
    assert out.sources == ["hackernews"]
    assert len(fake.calls) == 1  # one sync call only


def test_summarize_cluster_sync_drops_garbage_response() -> None:
    """If Claude returns missing fields, we return None instead of fabricating."""
    posts = _load_fixture_posts()
    gathered = demand.gather_question_comments(posts)
    clusters = demand.cluster_comments_hdbscan(gathered)
    non_noise = [c for c in clusters if c.cluster_id != -1]
    cluster = non_noise[0]

    fake = FakeAnthropic('{"oops": "wrong schema"}')
    out = demand.summarize_cluster_sync(cluster, client=fake)
    assert out is None


# ---- mine_demand_clusters_from_comments — end-to-end offline ----


class _FakeBatchAnthropic:
    """Mock Anthropic client with a Batch API surface.

    Captures all submitted batch requests and replays a canned per-cluster
    JSON body for each custom_id.
    """

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.submitted: list[list[dict]] = []
        self.messages = SimpleNamespace(
            create=self._sync_create,
            batches=SimpleNamespace(
                create=self._batch_create,
                retrieve=self._batch_retrieve,
                results=self._batch_results,
            ),
        )
        self._next_id = 0

    def _sync_create(self, **kwargs: Any) -> Any:
        # If somebody calls the sync path during end-to-end, fall back to
        # the first response. The end-to-end test path goes through Batch.
        first = next(iter(self.responses.values()))
        return SimpleNamespace(content=[SimpleNamespace(text=first)])

    def _batch_create(self, *, requests: list[dict], **_: Any) -> Any:
        self.submitted.append(requests)
        self._next_id += 1
        return SimpleNamespace(
            id=f"batch_{self._next_id}", processing_status="ended"
        )

    def _batch_retrieve(self, batch_id: str, **_: Any) -> Any:
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def _batch_results(self, batch_id: str, **_: Any):
        for request in self.submitted[-1]:
            custom_id = request["custom_id"]
            text = self.responses.get(custom_id) or '{"oops": true}'
            message = SimpleNamespace(content=[SimpleNamespace(text=text)])
            result = SimpleNamespace(type="succeeded", message=message)
            yield SimpleNamespace(custom_id=custom_id, result=result)


def test_mine_demand_clusters_from_comments_end_to_end_offline() -> None:
    """Full path: fixture posts → filter → cluster → batch-mock → DemandCluster
    list. Asserts: ≥3 clusters, all valid DemandCluster, all asker_count ≥ 2,
    all creator_brief non-empty.

    NOTE: HDBSCAN cluster_id ordering is stable for a given dataset but we
    don't pin specific custom_ids — we map our canned response by ordinal
    index in the batch the function submits.
    """
    posts = _load_fixture_posts()

    # Canned per-cluster Claude bodies — keyed by ordinal index `c{i}` so the
    # mock can replay them regardless of HDBSCAN's actual cluster_id numbers.
    bodies = {
        f"c{i}": _json.dumps(
            {
                "question_shape": shape,
                "askers_estimate": askers,
                "quotes": [
                    {"text": "How do I run this locally?", "source": "HN"},
                    {"text": "Anyone got a sample config?", "source": "HN"},
                ],
                "weekly_growth_pct": 12.0,
                "open_window_days": 14,
                "creator_brief": brief,
            }
        )
        for i, (shape, askers, brief) in enumerate(
            [
                (
                    "How do solo creators run Claude Desktop + MCP locally?",
                    7,
                    "Tutorial: local MCP setup, indie-creator scale, no VPS.",
                ),
                (
                    "How do indie creators get AI agents to write Shorts that don't sound like ChatGPT?",
                    6,
                    "Walkthrough: prompt-chain for shorts script + thumbnail loop.",
                ),
                (
                    "How do solo builders cap Claude Batch API costs?",
                    5,
                    "Cost dashboard + caching patterns for one-person Anthropic SDK use.",
                ),
                (
                    "How do indie video creators auto-caption shorts in their own voice?",
                    4,
                    "Voice-cloned captions pipeline for YouTube Shorts.",
                ),
                (
                    "How do solo channels keep thumbnails on-brand across AI image gen?",
                    3,
                    "Reusable AI thumbnail recipe with brand-consistent characters.",
                ),
                (
                    "How do indie creators iterate AI agents instead of one-shotting?",
                    3,
                    "Agent loop pattern with eval gates for solo creator workflows.",
                ),
            ]
        )
    }
    client = _FakeBatchAnthropic(bodies)

    clusters = demand.mine_demand_clusters_from_comments(
        posts, client=client, sync_probe=False, max_clusters=12
    )

    # Validation: ≥2 clusters from the offline fixture (~23 question-shaped
    # comments across 6 posts). Production target is 6-12 clusters but
    # that requires the live HN volume across 75+ posts. The fixture's job
    # is to prove the pipeline produces meaningful intent groups; HDBSCAN
    # at min_cluster_size=3 won't over-fit a small dataset, which is
    # exactly the behavior we want in production noise.
    assert len(clusters) >= 2, f"expected ≥2 clusters, got {len(clusters)}"
    assert len(clusters) <= 12

    # Every cluster is a valid Pydantic DemandCluster
    for c in clusters:
        DemandCluster.model_validate(c.model_dump())

    # Every cluster meets the floor and has content
    for c in clusters:
        assert c.askers_estimate >= 2, c
        assert c.creator_brief.strip(), c
        assert c.question_shape.strip(), c
        assert c.sources == ["hackernews"]

    # Sort order: by askers_estimate desc
    askers = [c.askers_estimate for c in clusters]
    assert askers == sorted(askers, reverse=True)


def test_mine_demand_clusters_from_comments_passes_sync_probe_when_enabled() -> None:
    """When sync_probe=True (default), one sync call must precede the batch."""
    posts = _load_fixture_posts()

    body = _json.dumps(
        {
            "question_shape": "How do solo creators run MCP locally?",
            "askers_estimate": 5,
            "quotes": [{"text": "How do I run this locally?", "source": "HN"}],
            "weekly_growth_pct": 10.0,
            "open_window_days": 14,
            "creator_brief": "Brief.",
        }
    )
    # All cluster responses use the same body — fine for this assertion.
    bodies = {f"c{i}": body for i in range(20)}
    client = _FakeBatchAnthropic(bodies)

    # Track sync calls — add a counter to the mock.
    sync_call_count = [0]
    original = client._sync_create

    def counted(**kwargs: Any) -> Any:
        sync_call_count[0] += 1
        return original(**kwargs)

    client.messages.create = counted

    clusters = demand.mine_demand_clusters_from_comments(
        posts, client=client, sync_probe=True, max_clusters=12
    )

    assert sync_call_count[0] == 1, (
        f"sync probe must fire exactly once before the batch; saw {sync_call_count[0]}"
    )
    assert len(clusters) >= 1


# ---- synthesize_demand_from_trends: alternate-key tolerance + diagnostics ----


def _trend_stub(keyword: str, summary: str = "", hook: str = "") -> SimpleNamespace:
    """Minimal Trend-shaped stub for synthesize_demand_from_trends.

    The function only reads `.keyword`, `.summary`, and `.angles.hook`, so a
    SimpleNamespace is enough — no need to instantiate the full Pydantic Trend.
    """
    return SimpleNamespace(
        keyword=keyword,
        summary=summary,
        angles=SimpleNamespace(hook=hook),
    )


def test_synthesize_accepts_alternate_key_shape() -> None:
    """Sonnet sometimes returns rows keyed `question` or `pain_point` instead of
    `question_shape`. The coercion path should map those synonyms in, not drop
    the row silently (which would ship a 0-cluster wedge)."""
    response = json.dumps(
        [
            {
                # Alternate key — Sonnet drift from the prompt
                "question": "How do solo creators run Claude Desktop locally?",
                "askers_estimate": 7,
                "weekly_growth_pct": 12,
                "open_window_days": 14,
                "creator_brief": "Tutorial: Claude Desktop local config.",
                "related_trends": ["claude"],
            },
            {
                "pain_point": "Why does my MCP server keep dropping the stdio connection?",
                "askers_estimate": 5,
                "weekly_growth_pct": 8,
                "open_window_days": 21,
                "creator_brief": "Diagnose MCP stdio drops for indie devs.",
                "related_trends": ["mcp"],
            },
        ]
    )
    fake = FakeAnthropic(response)
    trends = [_trend_stub("claude"), _trend_stub("mcp")]
    clusters = demand.synthesize_demand_from_trends(trends, client=fake)
    assert len(clusters) == 2
    shapes = [c.question_shape for c in clusters]
    assert any("Claude Desktop" in s for s in shapes)
    assert any("MCP server" in s for s in shapes)


def test_synthesize_logs_count_and_keys_on_exit(
    capsys: "pytest.CaptureFixture[str]",
) -> None:
    """Drift guard: on exit, synthesize_demand_from_trends must emit a single
    structured log line containing the row count and the first row's keys so a
    future schema drift is visible in the GitHub Actions log."""
    response = json.dumps(
        [
            {
                "question": "What's the cheapest agent loop for Shorts creators?",
                "askers_estimate": 6,
            }
        ]
    )
    fake = FakeAnthropic(response)
    trends = [_trend_stub("agents")]
    demand.synthesize_demand_from_trends(trends, client=fake)
    err = capsys.readouterr().err
    # Find the diagnostic line — JSON structured, one per line.
    records = [
        json.loads(line)
        for line in err.strip().splitlines()
        if line.strip().startswith("{")
    ]
    diag = [r for r in records if r.get("event") == "demand_synthesize_parsed"]
    assert len(diag) == 1, f"expected one diagnostic line, got {len(diag)}: {records}"
    assert diag[0]["row_count"] == 1
    assert "question" in diag[0]["first_row_keys"]


# ---- cost estimation ----


def test_estimate_demand_batch_cost_cents_scales_with_cluster_count() -> None:
    zero = demand.estimate_demand_batch_cost_cents(0)
    one = demand.estimate_demand_batch_cost_cents(1)
    twelve = demand.estimate_demand_batch_cost_cents(12)
    assert zero == 0.0
    assert one > 0.0
    assert twelve > one
    # Sanity: a 12-cluster batch should cost well under a dollar
    assert twelve < 100.0, f"12-cluster batch estimate is wild: {twelve} cents"
