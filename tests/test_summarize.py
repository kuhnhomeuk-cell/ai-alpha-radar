"""TDD for pipeline.summarize.

All tests run against a mock Anthropic client — no paid calls during pytest.
Live one-card inspection is a separate one-shot script.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline import summarize


def _make_card(**overrides) -> summarize.CardInput:
    defaults: dict[str, Any] = {
        "keyword": "world-model-agents",
        "cluster_label": "Autonomous Reasoning",
        "related_terms": ["world models", "agentic ai", "browser agents"],
        "arxiv_papers_7d": 8,
        "github_repos_7d": 5,
        "hn_posts_7d": 12,
        "velocity_score": 3.4,
        "saturation": 22.0,
        "convergence_detected": True,
        "lifecycle_stage": "builder",
        "user_niche": "AI tools for solo creators",
    }
    defaults.update(overrides)
    return summarize.CardInput(**defaults)


class FakeAnthropic:
    """Stub Anthropic client. Returns canned JSON for each prompt key."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, model: str, max_tokens: int, system: list, messages: list) -> Any:
        prompt_text = messages[0]["content"]
        self.calls.append(
            {"model": model, "max_tokens": max_tokens, "system": system, "prompt": prompt_text}
        )
        # Match against a prompt-key marker in the user prompt
        for key, payload in self._responses.items():
            if key in prompt_text:
                return SimpleNamespace(content=[SimpleNamespace(text=payload)])
        raise AssertionError(f"FakeAnthropic: no canned response matches prompt: {prompt_text[:80]}")


def test_prompt_a_contains_keyword_and_signal_counts() -> None:
    card = _make_card()
    p = summarize._build_prompt_a(card)
    assert "world-model-agents" in p
    assert "arxiv_papers_7d=8" in p
    assert "github_repos_7d=5" in p
    assert "hn_posts_7d=12" in p
    assert "Autonomous Reasoning" in p


def test_prompt_b_references_summary_and_niche() -> None:
    card = _make_card()
    p = summarize._build_prompt_b(card, summary="A pithy summary.")
    assert "A pithy summary." in p
    assert "AI tools for solo creators" in p


def test_prompt_c_references_lifecycle_velocity_saturation_convergence() -> None:
    card = _make_card()
    p = summarize._build_prompt_c(card)
    assert "builder" in p
    assert "3.4" in p
    assert "22" in p  # saturation may render as 22 or 22.0
    assert "True" in p or "true" in p.lower()


def test_prompt_d_references_summary_and_keyword() -> None:
    card = _make_card()
    p = summarize._build_prompt_d(card, summary="A pithy summary.")
    assert "world-model-agents" in p
    assert "A pithy summary." in p


def test_enrich_card_makes_four_sequential_haiku_calls() -> None:
    card = _make_card()
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "Agents that learn an internal world model.", "confidence": "high"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {
                    "hook": "The thinking AI you'll see everywhere in 6 weeks",
                    "contrarian": "World models are overhyped",
                    "tutorial": "Build a tiny world-model agent",
                }
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "high",
                    "peak_estimate_days": 21,
                    "risk_flag": "single-source signal",
                    "rationale": "Strong arxiv velocity but GitHub still nascent.",
                }
            ),
            "Explain this trend using one analogy": json.dumps(
                {"eli_creator": "Like a chess AI that imagines its next ten moves first."}
            ),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert len(fake.calls) == 4
    # all four calls use the Haiku model
    assert all(c["model"] == summarize.HAIKU_MODEL for c in fake.calls)
    # output stitches together correctly
    assert output.summary == "Agents that learn an internal world model."
    assert output.summary_confidence == "high"
    assert output.angles.hook.startswith("The thinking AI")
    assert output.angles.contrarian == "World models are overhyped"
    assert output.angles.tutorial == "Build a tiny world-model agent"
    assert output.angles.eli_creator.startswith("Like a chess AI")
    assert output.risk.breakout_likelihood == "high"
    assert output.risk.peak_estimate_days == 21


def test_enrich_card_strips_markdown_json_fences() -> None:
    card = _make_card()
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": '```json\n{"summary": "x", "confidence": "low"}\n```',
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "h", "contrarian": "c", "tutorial": "t"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "low",
                    "peak_estimate_days": None,
                    "risk_flag": "none",
                    "rationale": "tiny signal",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "e"}),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert output.summary == "x"
    assert output.summary_confidence == "low"


def test_system_prompt_injects_user_niche() -> None:
    card = _make_card(user_niche="AI tools for video editors")
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": '{"summary": "x", "confidence": "high"}',
            "Generate three YouTube Shorts angles": '{"hook":"h","contrarian":"c","tutorial":"t"}',
            "Estimate:": (
                '{"breakout_likelihood":"low","peak_estimate_days":null,'
                '"risk_flag":"none","rationale":"r"}'
            ),
            "Explain this trend using one analogy": '{"eli_creator":"e"}',
        }
    )
    summarize.enrich_card(card, client=fake)
    # Every call's system prompt should mention the niche
    for c in fake.calls:
        assert any("AI tools for video editors" in s["text"] for s in c["system"])


def test_system_prompt_has_cache_control_marker() -> None:
    card = _make_card()
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": '{"summary": "x", "confidence": "high"}',
            "Generate three YouTube Shorts angles": '{"hook":"h","contrarian":"c","tutorial":"t"}',
            "Estimate:": (
                '{"breakout_likelihood":"low","peak_estimate_days":null,'
                '"risk_flag":"none","rationale":"r"}'
            ),
            "Explain this trend using one analogy": '{"eli_creator":"e"}',
        }
    )
    summarize.enrich_card(card, client=fake)
    for c in fake.calls:
        sys_block = c["system"][0]
        assert sys_block.get("cache_control") == {"type": "ephemeral"}


def test_enrich_card_raises_when_response_unparseable() -> None:
    card = _make_card()
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": "this is not json at all",
            "Generate three YouTube Shorts angles": '{"hook":"h","contrarian":"c","tutorial":"t"}',
            "Estimate:": (
                '{"breakout_likelihood":"low","peak_estimate_days":null,'
                '"risk_flag":"none","rationale":"r"}'
            ),
            "Explain this trend using one analogy": '{"eli_creator":"e"}',
        }
    )
    with pytest.raises(summarize.ClaudeParseError):
        summarize.enrich_card(card, client=fake)
