"""TDD for pipeline.summarize.

All tests run against a mock Anthropic client — no paid calls during pytest.
Live one-card inspection is a separate one-shot script.
"""

from __future__ import annotations

import json
from pathlib import Path
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


def test_estimate_batch_cost_cents_zero_for_no_cards() -> None:
    assert summarize.estimate_batch_cost_cents(0) == 0.0


def test_estimate_batch_cost_cents_scales_linearly() -> None:
    one = summarize.estimate_batch_cost_cents(1)
    thirty = summarize.estimate_batch_cost_cents(30)
    assert one > 0
    assert abs(thirty - 30 * one) < 1e-9


def test_estimate_batch_cost_cents_30_cards_under_dollar() -> None:
    # Sanity check — 30 cards should never estimate above $1.00 with current
    # pricing assumptions. If pricing ever spikes, this test is the trip wire.
    assert summarize.estimate_batch_cost_cents(30) < 100.0


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
    # builder horizon = [30, 60] (audit 2.5); model's 21 clamps up to 30.
    assert output.risk.peak_estimate_days == 30


def test_peak_estimate_days_clamped_to_lifecycle_horizon() -> None:
    """Audit 2.5: out-of-range peak_estimate_days is clamped to the nearest
    lifecycle horizon bound."""
    card = _make_card(lifecycle_stage="whisper")  # whisper horizon = (14, 30)
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "x", "confidence": "medium"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "h", "contrarian": "c", "tutorial": "t"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "medium",
                    "peak_estimate_days": 180,  # way out of range for whisper
                    "risk_flag": "none",
                    "rationale": "x",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "e"}),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert output.risk.peak_estimate_days == 30, "should clamp 180 → upper bound 30"


def test_peak_estimate_days_passes_through_when_in_range() -> None:
    card = _make_card(lifecycle_stage="builder")  # builder horizon = (30, 60)
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "x", "confidence": "medium"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "h", "contrarian": "c", "tutorial": "t"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "medium",
                    "peak_estimate_days": 45,  # inside [30, 60]
                    "risk_flag": "none",
                    "rationale": "x",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "e"}),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert output.risk.peak_estimate_days == 45


def test_peak_estimate_days_null_preserved_for_commodity() -> None:
    """Audit 2.5: commodity has no horizon — passing peak stays None."""
    card = _make_card(lifecycle_stage="commodity")
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "x", "confidence": "low"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "h", "contrarian": "c", "tutorial": "t"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "low",
                    "peak_estimate_days": None,
                    "risk_flag": "none",
                    "rationale": "x",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "e"}),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert output.risk.peak_estimate_days is None


def test_summary_confidence_clamped_to_low_when_signal_below_three() -> None:
    """Audit 2.4: post-LLM hard clamp — total_signal<3 forces confidence=low
    even if the model returns 'high'."""
    card = _make_card(
        arxiv_papers_7d=1,
        github_repos_7d=0,
        hn_posts_7d=0,
        s2_citations_7d=0,
    )
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "tiny signal trend", "confidence": "high"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "h", "contrarian": "c", "tutorial": "t"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "low",
                    "peak_estimate_days": None,
                    "risk_flag": "single-source signal",
                    "rationale": "tiny",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "e"}),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert output.summary_confidence == "low", \
        "total_signal=1 must clamp model 'high' to 'low'"


def test_summary_confidence_unchanged_when_signal_sufficient() -> None:
    card = _make_card(
        arxiv_papers_7d=8,
        github_repos_7d=5,
        hn_posts_7d=12,
        s2_citations_7d=5,  # total_signal = 30
    )
    fake = FakeAnthropic(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "real signal", "confidence": "high"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "h", "contrarian": "c", "tutorial": "t"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "high",
                    "peak_estimate_days": 21,
                    "risk_flag": "none",
                    "rationale": "broad",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "e"}),
        }
    )
    output = summarize.enrich_card(card, client=fake)
    assert output.summary_confidence == "high"


def test_prompt_a_includes_evidence_floor_instruction_when_low_signal() -> None:
    """Audit 2.4: pre-LLM injection — prompt tells the model what total_signal
    is and what the confidence floor is."""
    card = _make_card(arxiv_papers_7d=0, github_repos_7d=1, hn_posts_7d=0, s2_citations_7d=0)
    p = summarize._build_prompt_a(card)
    assert "total_signal" in p
    assert "high" in p.lower()  # threshold language for high-confidence band


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


# ---------- Batch + briefing mocks ----------


class FakeBatchClient:
    """Stub for messages.batches.{create,retrieve,results}."""

    def __init__(self, prompt_responses: dict[str, str]) -> None:
        self.prompt_responses = prompt_responses
        self._counter = 0
        self._batches: dict[str, dict] = {}
        self.messages = SimpleNamespace(
            create=self._not_implemented,
            batches=SimpleNamespace(
                create=self._batch_create,
                retrieve=self._batch_retrieve,
                results=self._batch_results,
            ),
        )

    def _not_implemented(self, **_: Any) -> Any:
        raise AssertionError("sync path called in batch-only test")

    def _next_id(self) -> str:
        self._counter += 1
        return f"batch_{self._counter}"

    def _batch_create(self, *, requests: list[dict]) -> Any:
        batch_id = self._next_id()
        self._batches[batch_id] = {"requests": requests, "status": "ended"}
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def _batch_retrieve(self, batch_id: str) -> Any:
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def _batch_results(self, batch_id: str) -> Any:
        records = []
        for r in self._batches[batch_id]["requests"]:
            prompt_text = r["params"]["messages"][0]["content"]
            payload = None
            for key, p in self.prompt_responses.items():
                if key in prompt_text:
                    payload = p
                    break
            if payload is None:
                raise AssertionError(
                    f"FakeBatchClient: no canned response for prompt: {prompt_text[:80]}"
                )
            records.append(
                SimpleNamespace(
                    custom_id=r["custom_id"],
                    result=SimpleNamespace(
                        type="succeeded",
                        message=SimpleNamespace(
                            content=[SimpleNamespace(text=payload)]
                        ),
                    ),
                )
            )
        return iter(records)


def test_enrich_cards_batch_two_stage_orchestration() -> None:
    cards = [_make_card(keyword="alpha"), _make_card(keyword="beta")]
    fake = FakeBatchClient(
        {
            "Write a single-sentence summary": json.dumps(
                {"summary": "S", "confidence": "high"}
            ),
            "Generate three YouTube Shorts angles": json.dumps(
                {"hook": "H", "contrarian": "C", "tutorial": "T"}
            ),
            "Estimate:": json.dumps(
                {
                    "breakout_likelihood": "medium",
                    "peak_estimate_days": 30,
                    "risk_flag": "none",
                    "rationale": "r",
                }
            ),
            "Explain this trend using one analogy": json.dumps({"eli_creator": "E"}),
        }
    )
    outputs = summarize.enrich_cards_batch(cards, client=fake)
    assert set(outputs.keys()) == {0, 1}
    for o in outputs.values():
        assert o.summary == "S"
        assert o.angles.hook == "H"
        assert o.angles.eli_creator == "E"
        assert o.risk.breakout_likelihood == "medium"


def test_enrich_cards_batch_empty_list_returns_empty_dict() -> None:
    fake = FakeBatchClient({})
    assert summarize.enrich_cards_batch([], client=fake) == {}


_STANDARD_BATCH_RESPONSES = {
    "Write a single-sentence summary": json.dumps(
        {"summary": "S", "confidence": "high"}
    ),
    "Generate three YouTube Shorts angles": json.dumps(
        {"hook": "H", "contrarian": "C", "tutorial": "T"}
    ),
    "Estimate:": json.dumps(
        {
            "breakout_likelihood": "medium",
            "peak_estimate_days": 30,
            "risk_flag": "none",
            "rationale": "r",
        }
    ),
    "Explain this trend using one analogy": json.dumps({"eli_creator": "E"}),
}


def test_enrich_cards_batch_same_day_rerun_hits_cache(tmp_path: Path) -> None:
    """Audit 4.2 — second call with the same cards must not re-submit."""
    cards = [_make_card(keyword="alpha"), _make_card(keyword="beta")]
    cache_path = tmp_path / ".batch_state.json"

    fake1 = FakeBatchClient(_STANDARD_BATCH_RESPONSES)
    out1 = summarize.enrich_cards_batch(cards, client=fake1, cache_path=cache_path)
    assert set(out1.keys()) == {0, 1}
    # Two stages = two create calls
    assert len(fake1._batches) == 2

    fake2 = FakeBatchClient(_STANDARD_BATCH_RESPONSES)
    out2 = summarize.enrich_cards_batch(cards, client=fake2, cache_path=cache_path)
    assert set(out2.keys()) == {0, 1}
    # Same outputs, but zero new submissions because cache served both stages
    assert len(fake2._batches) == 0
    # Outputs equivalent
    for i in (0, 1):
        assert out1[i].summary == out2[i].summary
        assert out1[i].angles.hook == out2[i].angles.hook


def test_enrich_cards_batch_no_cache_path_is_backwards_compat() -> None:
    """Without cache_path, behavior matches pre-4.2 — every call resubmits."""
    cards = [_make_card(keyword="alpha")]
    fake = FakeBatchClient(_STANDARD_BATCH_RESPONSES)
    summarize.enrich_cards_batch(cards, client=fake)
    summarize.enrich_cards_batch(cards, client=fake)
    assert len(fake._batches) == 4  # 2 stages × 2 calls


def test_daily_briefing_calls_sonnet_and_parses() -> None:
    movers = [
        summarize.TrendMover(
            keyword="world-model-agents",
            lifecycle_stage="builder",
            velocity_score=3.4,
            velocity_acceleration=1.1,
            saturation=22,
        ),
        summarize.TrendMover(
            keyword="prompt-engineering",
            lifecycle_stage="commodity",
            velocity_score=0.8,
            velocity_acceleration=-0.3,
            saturation=82,
        ),
    ]
    canned = json.dumps(
        {
            "text": "What moved: world-model-agents. What died: prompt-engineering. "
            "What's emerging: small AI agents.",
            "moved_up": ["world-model-agents"],
            "moved_down": ["prompt-engineering"],
            "emerging": ["small-ai-agents"],
        }
    )
    fake = FakeAnthropic({"Today's tracked trends": canned})
    briefing = summarize.daily_briefing(movers, client=fake)
    assert briefing.moved_up == ["world-model-agents"]
    assert briefing.moved_down == ["prompt-engineering"]
    assert briefing.emerging == ["small-ai-agents"]
    assert briefing.generated_at.tzinfo is not None
    # daily briefing uses Sonnet
    assert fake.calls[0]["model"] == summarize.SONNET_MODEL


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
