"""TDD for pipeline.fetch.perplexity (Wave 5 — Sonar pain-point enrichment)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, perplexity
from pipeline.models import PainPoint

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "perplexity_sample.json"


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------- parse_response ----------


def test_parse_response_extracts_pain_points_in_rank_order() -> None:
    points = perplexity.parse_response(_load())
    assert len(points) == 3
    assert all(isinstance(p, PainPoint) for p in points)
    # Sonar returns ranked from most important; rank should be 1-based.
    assert [p.rank for p in points] == [1, 2, 3]
    assert "ComfyUI" in points[0].text
    assert points[0].source_url.startswith("https://")
    assert points[0].source_title


def test_parse_response_empty_choices_returns_empty() -> None:
    assert perplexity.parse_response({"choices": []}) == []


def test_parse_response_malformed_content_returns_empty() -> None:
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "I refuse to follow the JSON schema."}}
        ]
    }
    assert perplexity.parse_response(payload) == []


def test_parse_response_strips_markdown_codefence() -> None:
    """Sonar sometimes wraps JSON in ```json fences. Parser must strip them."""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        "```json\n"
                        '[{"text": "x", "source_url": "https://a.b/c", "source_title": "title"}]\n'
                        "```"
                    ),
                }
            }
        ]
    }
    points = perplexity.parse_response(payload)
    assert len(points) == 1
    assert points[0].text == "x"


def test_parse_response_skips_items_missing_required_fields() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        [
                            {"text": "ok", "source_url": "https://a.b/c", "source_title": "t1"},
                            {"text": "missing url"},  # malformed — skipped
                            {"text": "ok2", "source_url": "https://x.y/z", "source_title": "t2"},
                        ]
                    ),
                }
            }
        ]
    }
    points = perplexity.parse_response(payload)
    assert len(points) == 2
    assert [p.text for p in points] == ["ok", "ok2"]
    assert [p.rank for p in points] == [1, 2]


# ---------- estimate_cost_cents ----------


def test_estimate_cost_cents_returns_at_least_one_cent() -> None:
    """Sonar charges a per-request search fee plus token cost; floor is 1¢."""
    cost = perplexity.estimate_cost_cents(_load())
    assert isinstance(cost, int)
    assert cost >= 1


def test_estimate_cost_cents_missing_usage_returns_one() -> None:
    """If usage isn't reported, conservatively bill 1 cent for the search fee."""
    cost = perplexity.estimate_cost_cents({"choices": []})
    assert cost == 1


# ---------- HTTP-layer integration ----------


@respx.mock
def test_post_200_path_parses_payload(_no_sleep: None) -> None:
    route = respx.post(perplexity.PERPLEXITY_API_URL).mock(
        return_value=httpx.Response(200, json=_load()),
    )
    payload = perplexity._post(
        perplexity.PERPLEXITY_API_URL,
        body={"model": "sonar", "messages": [{"role": "user", "content": "hi"}]},
        key="test-key",
    )
    assert route.called
    # Outgoing request includes Bearer auth header.
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer test-key"
    assert sent.headers["content-type"].startswith("application/json")
    assert payload["model"] == "sonar"


@respx.mock
def test_post_honors_retry_after_on_429(_no_sleep: None) -> None:
    route = respx.post(perplexity.PERPLEXITY_API_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_load()),
        ]
    )
    payload = perplexity._post(
        perplexity.PERPLEXITY_API_URL,
        body={"model": "sonar", "messages": []},
        key="test-key",
    )
    assert route.call_count == 2
    assert "choices" in payload


@respx.mock
def test_post_500_exhausts_attempts(_no_sleep: None) -> None:
    route = respx.post(perplexity.PERPLEXITY_API_URL).mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        perplexity._post(
            perplexity.PERPLEXITY_API_URL,
            body={"model": "sonar", "messages": []},
            key="test-key",
        )
    assert route.call_count == 3


# ---------- fetch_pain_points (end-to-end) ----------


def test_fetch_pain_points_returns_empty_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per project convention, a missing API key downgrades to empty silently
    (logged) rather than crashing the pipeline."""
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    points, cost = perplexity.fetch_pain_points("ComfyUI")
    assert points == []
    assert cost == 0


@respx.mock
def test_fetch_pain_points_end_to_end_happy_path(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    respx.post(perplexity.PERPLEXITY_API_URL).mock(
        return_value=httpx.Response(200, json=_load()),
    )
    points, cost = perplexity.fetch_pain_points("ComfyUI")
    assert len(points) == 3
    assert points[0].rank == 1
    assert cost >= 1


@respx.mock
def test_fetch_pain_points_degrades_on_persistent_5xx(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """A persistent 5xx must NOT crash the whole pipeline — return empty + 0 cost."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    respx.post(perplexity.PERPLEXITY_API_URL).mock(
        return_value=httpx.Response(500),
    )
    points, cost = perplexity.fetch_pain_points("ComfyUI")
    assert points == []
    assert cost == 0
