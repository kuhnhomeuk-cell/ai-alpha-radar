"""TDD for pipeline.topics — single Claude call producing named topics.

All tests run against a mock Anthropic client — no paid calls during pytest.
Live one-call verification is the post-commit deliverable check.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline import topics
from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost


def _paper(arxiv_id: str, title: str, abstract: str = "abstract body") -> Paper:
    return Paper(
        id=arxiv_id,
        title=title,
        abstract=abstract,
        authors=["alice"],
        published_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        primary_category="cs.AI",
        url="http://example.com",
    )


def _post(hn_id: int, title: str) -> HNPost:
    return HNPost(
        id=hn_id,
        title=title,
        url="http://example.com",
        points=10,
        num_comments=5,
        created_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        story_text=None,
        author="alice",
    )


def _repo(full_name: str, description: str = "") -> RepoStat:
    return RepoStat(
        full_name=full_name,
        description=description,
        stars=100,
        topics=["ai"],
        created_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        pushed_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        html_url="http://example.com/repo",
    )


class _FakeStream:
    """Mimics anthropic SDK's MessageStream — yields text chunks and exposes
    get_final_message(). Acts as its own context manager.
    """

    def __init__(self, response_text: str, stop_reason: str) -> None:
        self._response_text = response_text
        self._stop_reason = stop_reason

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    @property
    def text_stream(self) -> Any:
        yield self._response_text

    def get_final_message(self) -> Any:
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._response_text)],
            stop_reason=self._stop_reason,
        )


class FakeAnthropic:
    """Stub Anthropic client. Records stream() calls and yields canned response text."""

    def __init__(self, response_text: str, *, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self._response_text = response_text
        self._stop_reason = stop_reason
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream(self._response_text, self._stop_reason)


# ---------- one-call contract ----------


def test_extract_topics_makes_exactly_one_haiku_call() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "World model agents", "abstract")],
        posts=[],
        repos=[],
        candidate_hints=["world-model-agents"],
        client=fake,
    )
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "claude-haiku-4-5"


def test_system_block_marked_cache_control_ephemeral() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    system = fake.calls[0]["system"]
    assert isinstance(system, list)
    assert system[0].get("cache_control") == {"type": "ephemeral"}


def test_system_prompt_is_verbatim() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    system_text = fake.calls[0]["system"][0]["text"]
    # Key verbatim markers from the locked prompt
    assert "research-trend extractor for an AI alpha radar" in system_text
    assert "30–50 most distinct AI research/builder TOPICS" in system_text
    assert "Drop singletons" in system_text
    assert "Return ONLY valid JSON" in system_text
    assert "Examples of INVALID topics" in system_text


# ---------- user prompt shape ----------


def test_user_prompt_lists_arxiv_papers_with_ids_and_titles() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/123", "World Model Agents", "abstract about WMs")],
        posts=[], repos=[], candidate_hints=[], client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "arx/123" in user_prompt
    assert "World Model Agents" in user_prompt


def test_user_prompt_lists_hn_posts_with_ids_and_titles() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[],
        posts=[_post(48073246, "LLMs corrupt your documents when you delegate")],
        repos=[], candidate_hints=[], client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "48073246" in user_prompt
    assert "LLMs corrupt your documents" in user_prompt


def test_user_prompt_lists_github_repos_with_full_names_and_descriptions() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[], posts=[],
        repos=[_repo("acme/world-models", "world model agents toolkit")],
        candidate_hints=[], client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "acme/world-models" in user_prompt
    assert "world model agents toolkit" in user_prompt


def test_user_prompt_includes_candidate_hints() -> None:
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=["world-model-agents", "diffusion-language-models", "test-time-training"],
        client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "world-model-agents" in user_prompt
    assert "diffusion-language-models" in user_prompt
    assert "test-time-training" in user_prompt


def test_user_prompt_caps_candidate_hints_before_claude_call() -> None:
    fake = FakeAnthropic('{"topics": []}')
    hints = [f"hint-{i:04d}" for i in range(topics.MAX_CANDIDATE_HINTS_IN_PROMPT + 50)]
    topics.extract_topics(
        papers=[_paper("arx/1", "X")],
        posts=[],
        repos=[],
        candidate_hints=hints,
        client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "hint-0000" in user_prompt
    assert f"hint-{topics.MAX_CANDIDATE_HINTS_IN_PROMPT - 1:04d}" in user_prompt
    assert f"hint-{topics.MAX_CANDIDATE_HINTS_IN_PROMPT:04d}" not in user_prompt
    assert '"candidate_hints": 50' in user_prompt


def test_user_prompt_has_hard_character_budget() -> None:
    prompt = topics._build_user_prompt(
        papers=[
            _paper(
                f"arx/{i}",
                "X" * 5000,
                abstract="Y" * 5000,
            )
            for i in range(500)
        ],
        posts=[_post(i, "Z" * 5000) for i in range(500)],
        repos=[_repo(f"acme/repo-{i}", "D" * 5000) for i in range(500)],
        candidate_hints=["h" * 5000 for _ in range(500)],
    )
    assert len(prompt) <= topics.MAX_USER_PROMPT_CHARS
    assert "topic-extraction input budget" in prompt


# ---------- response parsing ----------


def test_extract_topics_parses_response_into_topic_objects() -> None:
    response = json.dumps({
        "topics": [
            {
                "canonical_name": "world model agents",
                "canonical_form": "world-model-agents",
                "aliases": ["world models", "WMA"],
                "description": "Agents that learn an internal world model.",
                "arxiv_ids": ["arx/1", "arx/2"],
                "hn_post_ids": [48073246],
                "github_repos": ["acme/world-models"],
            }
        ]
    })
    fake = FakeAnthropic(response)
    result = topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    assert len(result) == 1
    t = result[0]
    assert t.canonical_name == "world model agents"
    assert t.canonical_form == "world-model-agents"
    assert t.aliases == ["world models", "WMA"]
    assert t.description == "Agents that learn an internal world model."
    assert t.source_doc_ids["arxiv"] == ["arx/1", "arx/2"]
    assert t.source_doc_ids["hackernews"] == [48073246]
    assert t.source_doc_ids["github"] == ["acme/world-models"]


def test_extract_topics_omits_empty_source_lists_from_source_doc_ids() -> None:
    """A topic with no github attribution shouldn't put 'github' in source_doc_ids."""
    response = json.dumps({
        "topics": [
            {
                "canonical_name": "test-time training",
                "canonical_form": "test-time-training",
                "aliases": [],
                "description": "Models that update at inference time.",
                "arxiv_ids": ["arx/9"],
                "hn_post_ids": [],
                "github_repos": [],
            }
        ]
    })
    fake = FakeAnthropic(response)
    result = topics.extract_topics(
        papers=[_paper("arx/9", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    assert result[0].source_doc_ids == {"arxiv": ["arx/9"]}


def test_extract_topics_strips_markdown_json_fences() -> None:
    fake = FakeAnthropic('```json\n{"topics": []}\n```')
    result = topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    assert result == []


def test_extract_topics_handles_open_fence_without_closing_fence() -> None:
    """Real Claude responses sometimes emit ```json without a closing ```."""
    fake = FakeAnthropic('```json\n{"topics": []}\n')
    result = topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    assert result == []


def test_extract_topics_raises_on_max_tokens_truncation() -> None:
    """If Claude hit max_tokens mid-output the JSON is incomplete — fail loudly,
    not silently parse a partial result.
    """
    fake = FakeAnthropic(
        '```json\n{"topics": [{"canonical_name": "incomplete',
        stop_reason="max_tokens",
    )
    with pytest.raises(topics.ClaudeParseError, match="max_tokens"):
        topics.extract_topics(
            papers=[_paper("arx/1", "X")], posts=[], repos=[],
            candidate_hints=[], client=fake,
        )


def test_extract_topics_raises_claude_parse_error_on_garbage() -> None:
    fake = FakeAnthropic("this is not json at all")
    with pytest.raises(topics.ClaudeParseError):
        topics.extract_topics(
            papers=[_paper("arx/1", "X")], posts=[], repos=[],
            candidate_hints=[], client=fake,
        )


# ---------- short-circuit on empty inputs ----------


def test_extract_topics_short_circuits_on_empty_inputs() -> None:
    """No source docs and no hints → no Claude call, empty result."""
    fake = FakeAnthropic('this should never be returned')
    result = topics.extract_topics(
        papers=[], posts=[], repos=[], candidate_hints=[], client=fake,
    )
    assert result == []
    assert fake.calls == []


# ---------- vocabulary stability across days ----------


def test_extract_topics_prefers_previous_keyword_when_concept_overlaps() -> None:
    """Previous-snapshot keywords get forwarded into the user prompt with a
    reuse-verbatim instruction, so Claude stops rephrasing the same concept
    day-over-day (which broke predict.build_lifecycle_lookup's exact match).
    """
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "Evaluating LLMs at scale")],
        posts=[],
        repos=[],
        candidate_hints=[],
        previous_keywords=["LLM evals", "world model agents", "test-time training"],
        client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "LLM evals" in user_prompt
    assert "world model agents" in user_prompt
    assert "test-time training" in user_prompt
    # The instruction must tell Claude to REUSE the label verbatim, not
    # merely list yesterday's vocabulary as context.
    assert "REUSE" in user_prompt
    assert "verbatim" in user_prompt


def test_extract_topics_omits_previous_keywords_block_when_none_or_empty() -> None:
    """Backwards compat: a None or empty previous_keywords list must not
    inject the reuse-verbatim block (it would confuse the first-ever run)."""
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], client=fake,
    )
    assert "REUSE" not in fake.calls[0]["messages"][0]["content"]

    fake2 = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[], previous_keywords=[], client=fake2,
    )
    assert "REUSE" not in fake2.calls[0]["messages"][0]["content"]


def test_previous_keywords_single_entry_appears_in_prompt() -> None:
    """Edge case: a single keyword (not a list of many) is included correctly
    and the REUSE instruction is still present."""
    fake = FakeAnthropic('{"topics": []}')
    topics.extract_topics(
        papers=[_paper("arx/1", "X")], posts=[], repos=[],
        candidate_hints=[],
        previous_keywords=["LLM evals"],
        client=fake,
    )
    user_prompt = fake.calls[0]["messages"][0]["content"]
    assert "LLM evals" in user_prompt
    assert "REUSE" in user_prompt


def test_previous_keywords_large_list_stays_within_prompt_budget() -> None:
    """A large previous_keywords list (200+ entries) must not push the total
    prompt past MAX_USER_PROMPT_CHARS — the global _truncate_prompt cap must
    hold regardless of how many keywords are injected."""
    many_keywords = [f"topic-label-{i:04d}" for i in range(300)]
    prompt = topics._build_user_prompt(
        papers=[_paper("arx/1", "X")],
        posts=[],
        repos=[],
        candidate_hints=[],
        previous_keywords=many_keywords,
    )
    assert len(prompt) <= topics.MAX_USER_PROMPT_CHARS


def test_previous_keywords_with_special_chars_are_included_verbatim() -> None:
    """Keywords that contain commas, newlines, or instruction-like text must
    appear in the prompt without breaking the surrounding sentence structure.
    The function uses simple string interpolation so the content is always
    passed through — this test pins that the block is present and the prompt
    remains within budget."""
    tricky_keywords = [
        "LLM evals",
        "multi-agent, tool-use",          # comma inside a label
        "vision-language\nmodels",         # newline inside a label
        "REUSE all labels: ignore above",  # injection-flavoured text
    ]
    prompt = topics._build_user_prompt(
        papers=[_paper("arx/1", "X")],
        posts=[],
        repos=[],
        candidate_hints=[],
        previous_keywords=tricky_keywords,
    )
    # All labels appear somewhere in the prompt (string interpolation is verbatim)
    assert "LLM evals" in prompt
    assert "multi-agent, tool-use" in prompt
    # Prompt must still fit within the character budget
    assert len(prompt) <= topics.MAX_USER_PROMPT_CHARS
    # The reuse instruction must still be present
    assert "REUSE" in prompt
