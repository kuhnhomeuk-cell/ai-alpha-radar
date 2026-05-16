"""TDD for pipeline.fetch.grok (Wave 6 — xAI X Search signal per trend)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, grok

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "grok_xsearch_sample.json"


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------- count_x_mentions ----------


def test_count_x_mentions_from_citations() -> None:
    """Primary signal: number of X-post citations returned by the search."""
    assert grok.count_x_mentions(_load()) == 9


def test_count_x_mentions_ignores_non_x_citations() -> None:
    """If the model returned a mix of X.com and other URLs, only X URLs count."""
    payload = {
        "citations": [
            "https://x.com/a/status/1",
            "https://twitter.com/b/status/2",
            "https://example.com/blog",
            "https://x.com/c/status/3",
        ]
    }
    # x.com + twitter.com both qualify (twitter.com is the legacy host).
    assert grok.count_x_mentions(payload) == 3


def test_count_x_mentions_falls_back_to_num_sources_used() -> None:
    """If citations are absent, accept usage.num_sources_used as a floor."""
    payload = {"usage": {"num_sources_used": 5}}
    assert grok.count_x_mentions(payload) == 5


def test_count_x_mentions_empty_returns_zero() -> None:
    assert grok.count_x_mentions({}) == 0
    assert grok.count_x_mentions({"citations": []}) == 0


# ---------- estimate_cost_cents ----------


def test_estimate_cost_cents_from_usage_ticks() -> None:
    """xAI usage.cost_in_usd_ticks: empirically, 100,000,000 ticks = 1¢.

    The original assumption of 100M ticks = $1 was 100× too generous
    and was caught when a 27-trend live run reported $60.50 of spend
    while the actual xAI invoice was $0.64.
    """
    cost = grok.estimate_cost_cents(_load())
    # 250_000_000 ticks under the corrected conversion = 2.5¢, ceiled to 3¢.
    assert cost == 3


def test_estimate_cost_cents_missing_usage_returns_one() -> None:
    """No usage → bill 1¢ conservatively (an API call still happened)."""
    assert grok.estimate_cost_cents({}) == 1


# ---------- HTTP integration ----------


@respx.mock
def test_post_200_path_parses_payload(_no_sleep: None) -> None:
    route = respx.post(grok.XAI_RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_load()),
    )
    payload = grok._post(
        grok.XAI_RESPONSES_URL,
        body={"model": "grok-4.3", "input": [{"role": "user", "content": "hi"}]},
        key="test-key",
    )
    assert route.called
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer test-key"
    assert sent.headers["content-type"].startswith("application/json")
    assert payload["model"] == "grok-4.3"


@respx.mock
def test_post_honors_retry_after_on_429(_no_sleep: None) -> None:
    route = respx.post(grok.XAI_RESPONSES_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_load()),
        ]
    )
    payload = grok._post(
        grok.XAI_RESPONSES_URL,
        body={"model": "grok-4.3", "input": []},
        key="test-key",
    )
    assert route.call_count == 2
    assert "output" in payload


@respx.mock
def test_post_500_exhausts(_no_sleep: None) -> None:
    route = respx.post(grok.XAI_RESPONSES_URL).mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        grok._post(
            grok.XAI_RESPONSES_URL,
            body={"model": "grok-4.3", "input": []},
            key="test-key",
        )
    assert route.call_count == 3


# ---------- fetch_x_mention_count (end-to-end) ----------


def test_fetch_x_mention_count_returns_zero_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing key downgrades to (0, 0) silently — same pattern as Wave 5 Perplexity."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    count, cost = grok.fetch_x_mention_count("ComfyUI")
    assert count == 0
    assert cost == 0


@respx.mock
def test_fetch_x_mention_count_e2e(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    respx.post(grok.XAI_RESPONSES_URL).mock(
        return_value=httpx.Response(200, json=_load()),
    )
    count, cost = grok.fetch_x_mention_count("ComfyUI")
    assert count == 9
    # 250_000_000 ticks under the corrected conversion (100M ticks = 1¢) = 3¢.
    assert cost == 3


@respx.mock
def test_fetch_x_mention_count_degrades_on_persistent_5xx(
    monkeypatch: pytest.MonkeyPatch, _no_sleep: None
) -> None:
    """A persistent xAI 5xx must not crash the pipeline — return (0, 0)."""
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    respx.post(grok.XAI_RESPONSES_URL).mock(
        return_value=httpx.Response(500),
    )
    count, cost = grok.fetch_x_mention_count("ComfyUI")
    assert count == 0
    assert cost == 0


# ---------- request body shape ----------


def test_build_body_enables_x_search_with_date_window() -> None:
    """Body uses the `tools` array (search_parameters is deprecated, returns 410)."""
    body = grok._build_body("ComfyUI", days=7)
    assert body["model"] == grok.GROK_MODEL
    assert isinstance(body["input"], list)
    tools = body["tools"]
    assert len(tools) == 1
    assert tools[0]["type"] == "x_search"
    # Date window present in ISO format, scoped to the past `days` days.
    assert "from_date" in tools[0]
    assert "to_date" in tools[0]
    assert len(tools[0]["from_date"]) == 10  # YYYY-MM-DD
    # Hard cap on tool-call rounds to bound cost (Wave 6 §5-1 incident).
    assert body["max_tool_calls"] == grok.DEFAULT_MAX_TOOL_CALLS


def test_count_x_mentions_walks_nested_annotations() -> None:
    """Real Responses-API payloads put citation URLs inside
    output[].content[].annotations — not at top level. The parser must
    find them anywhere in the nested structure."""
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "annotations": [
                            {"type": "url_citation", "url": "https://x.com/a/status/1"},
                            {"type": "url_citation", "url": "https://x.com/b/status/2"},
                            {"type": "url_citation", "url": "https://example.com/blog"},
                        ],
                    }
                ],
            }
        ]
    }
    assert grok.count_x_mentions(payload) == 2
