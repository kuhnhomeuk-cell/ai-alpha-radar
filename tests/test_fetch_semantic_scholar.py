"""TDD for pipeline.fetch.semantic_scholar against a cached batch response.

The S2 batch endpoint returns a list parallel to the input ids list, with
null entries for any id it hasn't indexed yet. The fixture mixes well-known
indexed papers (Transformer, GPT-3, InstructGPT, Gemini 1.5, Mistral 7B)
with two too-new arxiv IDs that round-trip as null — exercising both paths.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, semantic_scholar

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s2_sample.json"


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(semantic_scholar.time, "sleep", lambda _s: None)

# Order MUST match the order in tests/fixtures/s2_sample.json — the S2 batch
# endpoint returns entries parallel to the input ids list.
FIXTURE_IDS = [
    "ARXIV:1706.03762",  # Transformer
    "ARXIV:2005.14165",  # GPT-3
    "ARXIV:2203.02155",  # InstructGPT
    "ARXIV:2403.05530",  # Gemini 1.5
    "ARXIV:2310.06825",  # Mistral 7B
    "ARXIV:2605.12493",  # too new - null
    "ARXIV:2605.12492",  # too new - null
]


def _load() -> list:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_batch_response_returns_only_indexed_entries() -> None:
    enriched = semantic_scholar.parse_batch_response(FIXTURE_IDS, _load())
    assert len(enriched) == 5, "5 of 7 fixture entries are non-null"
    for aid in ["ARXIV:1706.03762", "ARXIV:2005.14165", "ARXIV:2203.02155"]:
        assert aid in enriched


def test_unindexed_ids_absent_from_result() -> None:
    enriched = semantic_scholar.parse_batch_response(FIXTURE_IDS, _load())
    assert "ARXIV:2605.12493" not in enriched
    assert "ARXIV:2605.12492" not in enriched


def test_each_citation_info_has_non_negative_counts() -> None:
    enriched = semantic_scholar.parse_batch_response(FIXTURE_IDS, _load())
    for aid, info in enriched.items():
        assert info.citation_count >= 0, f"{aid} has negative citation_count"
        assert info.influential_citation_count >= 0
        assert info.references_count >= 0


def test_transformer_paper_has_many_citations() -> None:
    enriched = semantic_scholar.parse_batch_response(FIXTURE_IDS, _load())
    transformer = enriched["ARXIV:1706.03762"]
    assert transformer.citation_count > 1000


def test_empty_input_returns_empty_dict() -> None:
    assert semantic_scholar.parse_batch_response([], []) == {}


def test_enrich_papers_chunks_batches_over_500(monkeypatch) -> None:
    """Audit item 2.8: ids beyond the S2 batch cap must be split into chunks
    and merged, not raise ValueError."""
    from pipeline.fetch.semantic_scholar import (
        CitationInfo,
        S2_BATCH_LIMIT,
        enrich_papers,
    )

    big_input = [f"{2200000 + i}" for i in range(S2_BATCH_LIMIT + 100)]  # 600 ids
    captured_chunks: list[list[str]] = []

    def fake_post_batch(chunk, api_key):
        captured_chunks.append(list(chunk))
        return {aid: CitationInfo(citation_count=1, influential_citation_count=0, references_count=0) for aid in chunk}

    monkeypatch.setattr(semantic_scholar, "_post_batch", fake_post_batch)
    out = enrich_papers(big_input)
    assert len(captured_chunks) == 2
    assert len(captured_chunks[0]) == S2_BATCH_LIMIT
    assert len(captured_chunks[1]) == 100
    assert len(out) == S2_BATCH_LIMIT + 100


def test_prefix_arxiv_ids_handles_bare_and_url_and_already_prefixed() -> None:
    inputs = ["1706.03762", "http://arxiv.org/abs/2005.14165v1", "ARXIV:2203.02155v2"]
    out = semantic_scholar._prefix_arxiv_ids(inputs)
    assert out == ["ARXIV:1706.03762", "ARXIV:2005.14165", "ARXIV:2203.02155"]


def test_prefix_arxiv_ids_preserves_old_style_category_ids() -> None:
    inputs = ["http://arxiv.org/abs/cs/9901001v1"]
    assert semantic_scholar._prefix_arxiv_ids(inputs) == ["ARXIV:cs/9901001"]


def test_enrich_papers_splits_400_batches_to_skip_bad_ids(monkeypatch) -> None:
    from pipeline.fetch.semantic_scholar import CitationInfo

    def fake_post_batch(chunk, api_key):
        if "bad-id" in chunk:
            request = httpx.Request("POST", semantic_scholar.S2_BATCH_URL)
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)
        return {
            aid: CitationInfo(
                citation_count=1,
                influential_citation_count=0,
                references_count=0,
            )
            for aid in chunk
        }

    monkeypatch.setattr(semantic_scholar, "_post_batch", fake_post_batch)
    enriched = semantic_scholar.enrich_papers(["1706.03762", "bad-id", "2005.14165"])
    assert set(enriched) == {"1706.03762", "2005.14165"}


# ---------- audit 4.5 — HTTP-layer integration tests ----------


@respx.mock
def test_enrich_papers_200_path(_no_sleep: None) -> None:
    route = respx.post(semantic_scholar.S2_BATCH_URL).mock(
        return_value=httpx.Response(200, json=_load())
    )
    enriched = semantic_scholar.enrich_papers(FIXTURE_IDS)
    assert route.called
    assert len(enriched) == 5


@respx.mock
def test_enrich_papers_honors_retry_after(_no_sleep: None) -> None:
    route = respx.post(semantic_scholar.S2_BATCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_load()),
        ]
    )
    enriched = semantic_scholar.enrich_papers(FIXTURE_IDS)
    assert route.call_count == 2
    assert len(enriched) == 5


@respx.mock
def test_enrich_papers_500_exhausts(_no_sleep: None) -> None:
    route = respx.post(semantic_scholar.S2_BATCH_URL).mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        semantic_scholar.enrich_papers(FIXTURE_IDS)
    assert route.call_count == 3


@respx.mock
def test_enrich_papers_malformed_body_returns_empty(_no_sleep: None) -> None:
    # All-null parallel-array response — every id is "unindexed".
    respx.post(semantic_scholar.S2_BATCH_URL).mock(
        return_value=httpx.Response(200, json=[None] * len(FIXTURE_IDS)),
    )
    enriched = semantic_scholar.enrich_papers(FIXTURE_IDS)
    assert enriched == {}
