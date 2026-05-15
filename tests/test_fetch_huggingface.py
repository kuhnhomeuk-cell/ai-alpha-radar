"""TDD for pipeline.fetch.huggingface — Hugging Face Hub trending fetcher."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, huggingface

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hf_sample.json"


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(huggingface.time, "sleep", lambda _s: None)


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_response_returns_only_public_models() -> None:
    items = huggingface.parse_search_response(_load())
    # Fixture has 6 entries, 1 is private.
    assert len(items) == 5
    assert all(not m.private for m in items)


def test_parse_response_extracts_likes_downloads_tags() -> None:
    items = huggingface.parse_search_response(_load())
    llama = next(m for m in items if "Llama-3.3" in m.id)
    assert llama.likes == 2340
    assert llama.downloads == 1456000
    assert "text-generation" in llama.tags


def test_parse_response_handles_missing_optional_fields() -> None:
    raw = [{"id": "user/model", "modelId": "user/model"}]
    items = huggingface.parse_search_response(raw)
    assert len(items) == 1
    assert items[0].likes == 0
    assert items[0].downloads == 0
    assert items[0].tags == []


def test_model_name_extraction_drops_org_prefix() -> None:
    items = huggingface.parse_search_response(_load())
    names = [huggingface.model_name(m) for m in items]
    assert "Llama-3.3-70B-Instruct" in names
    assert "FLUX.1-dev" in names
    assert all("/" not in n for n in names)


def test_empty_response_returns_empty_list() -> None:
    assert huggingface.parse_search_response([]) == []


# ---------- audit 4.5 — HTTP-layer integration tests ----------


@respx.mock
def test_fetch_trending_models_200_path(_no_sleep: None) -> None:
    route = respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=_load()),
    )
    items = huggingface.fetch_trending_models(limit=20)
    assert route.called
    request = route.calls.last.request
    assert "sort=trendingScore" in str(request.url)
    assert "direction=-1" in str(request.url)
    assert len(items) == 5  # private filtered


@respx.mock
def test_fetch_trending_models_honors_retry_after(_no_sleep: None) -> None:
    route = respx.get("https://huggingface.co/api/models").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_load()),
        ]
    )
    items = huggingface.fetch_trending_models(limit=20)
    assert route.call_count == 2
    assert len(items) == 5


@respx.mock
def test_fetch_trending_models_500_exhausts(_no_sleep: None) -> None:
    route = respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        huggingface.fetch_trending_models(limit=20)
    assert route.call_count == 3


@respx.mock
def test_fetch_trending_models_malformed_body_returns_empty(_no_sleep: None) -> None:
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=[]),
    )
    items = huggingface.fetch_trending_models(limit=20)
    assert items == []


# ---------- v0.2.0 — download velocity layer ----------


def test_models_warming_up_until_velocity_attached() -> None:
    items = huggingface.parse_search_response(_load())
    for m in items:
        assert m.warming_up is True
        assert m.downloads_7d_delta is None


def test_compute_download_velocity_annotates_known_models() -> None:
    items = huggingface.parse_search_response(_load())
    # Build a prior map covering 2 of the 5 public models. Lower their prior
    # downloads by 1000 so we get a clean +1000 delta.
    known = [m for m in items[:2]]
    prior = {m.id: max(m.downloads - 1000, 0) for m in known}
    annotated = huggingface.compute_download_velocity(items, prior_downloads=prior)
    by_id = {m.id: m for m in annotated}

    for m in known:
        result = by_id[m.id]
        assert result.warming_up is False
        # delta is either +1000 or, if prior went to 0 floor, equals current.
        assert result.downloads_7d_delta == 1000 or result.downloads_7d_delta == m.downloads

    # Models not in the prior map stay warming up.
    for m in items[2:]:
        result = by_id[m.id]
        assert result.warming_up is True
        assert result.downloads_7d_delta is None


def test_load_prior_download_map_missing_file_returns_empty(tmp_path: Path) -> None:
    assert huggingface.load_prior_download_map(tmp_path / "nonexistent.json") == {}


def test_load_prior_download_map_reads_meta(tmp_path: Path) -> None:
    snap = tmp_path / "2026-05-08.json"
    snap.write_text(json.dumps({
        "meta": {"hf_downloads": {"meta-llama/Llama-3.3-70B-Instruct": 1_450_000}}
    }))
    result = huggingface.load_prior_download_map(snap)
    assert result == {"meta-llama/Llama-3.3-70B-Instruct": 1_450_000}
