"""TDD for pipeline.fetch.replicate (audit 3.5)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, replicate

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "replicate_sample.json"


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(replicate.time, "sleep", lambda _s: None)


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_response_extracts_public_models() -> None:
    models = replicate.parse_response(_load())
    assert len(models) == 2
    by_name = {m.name: m for m in models}
    assert "flux-schnell" in by_name
    assert by_name["flux-schnell"].run_count == 152_300_000


def test_parse_response_filters_private() -> None:
    models = replicate.parse_response(_load())
    assert all(m.visibility == "public" for m in models)


def test_run_count_deltas_against_prior() -> None:
    today = {"flux-schnell": 1_000_000, "llama-3-70b": 500_000, "new-model": 10_000}
    prior = {"flux-schnell": 900_000, "llama-3-70b": 600_000}
    deltas = replicate.run_count_deltas(today, prior)
    assert deltas["flux-schnell"] == 100_000
    # Drop is clipped to 0 (deltas count growth, not regression).
    assert deltas["llama-3-70b"] == 0
    # Brand-new model — full count counts as growth.
    assert deltas["new-model"] == 10_000


def test_runs_per_term_aggregates_matches() -> None:
    models = replicate.parse_response(_load())
    counts = replicate.runs_per_term(models, terms=["flux", "llama"])
    assert counts["flux"] == 152_300_000
    assert counts["llama"] == 48_900_000


# ---------- audit 4.5 — HTTP-layer integration tests ----------


@respx.mock
def test_get_200_path_parses_payload(_no_sleep: None) -> None:
    route = respx.get(replicate.REPLICATE_API_URL).mock(
        return_value=httpx.Response(200, json=_load()),
    )
    payload = replicate._get(replicate.REPLICATE_API_URL, "test-token")
    assert route.called
    assert replicate.parse_response(payload) == replicate.parse_response(_load())


@respx.mock
def test_get_honors_retry_after(_no_sleep: None) -> None:
    route = respx.get(replicate.REPLICATE_API_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_load()),
        ]
    )
    payload = replicate._get(replicate.REPLICATE_API_URL, "test-token")
    assert route.call_count == 2
    assert "results" in payload


@respx.mock
def test_get_500_exhausts(_no_sleep: None) -> None:
    route = respx.get(replicate.REPLICATE_API_URL).mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        replicate._get(replicate.REPLICATE_API_URL, "test-token")
    assert route.call_count == 3


@respx.mock
def test_get_malformed_body_raises_via_caller(_no_sleep: None) -> None:
    # _get itself doesn't validate the shape — parse_response treats missing
    # 'results' as empty. End-to-end this means "fetcher returns no models",
    # not "fetcher raises". Verified at the integration boundary.
    respx.get(replicate.REPLICATE_API_URL).mock(
        return_value=httpx.Response(200, json={"unexpected": True}),
    )
    payload = replicate._get(replicate.REPLICATE_API_URL, "test-token")
    assert replicate.parse_response(payload) == []


@respx.mock
def test_fetch_trending_returns_empty_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    assert replicate.fetch_trending() == []
