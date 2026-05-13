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
