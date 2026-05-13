"""TDD for pipeline.fetch.semantic_scholar against a cached batch response.

The S2 batch endpoint returns a list parallel to the input ids list, with
null entries for any id it hasn't indexed yet. The fixture mixes well-known
indexed papers (Transformer, GPT-3, InstructGPT, Gemini 1.5, Mistral 7B)
with two too-new arxiv IDs that round-trip as null — exercising both paths.
"""

import json
from pathlib import Path

from pipeline.fetch import semantic_scholar

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s2_sample.json"

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


def test_prefix_arxiv_ids_handles_bare_and_url_and_already_prefixed() -> None:
    inputs = ["1706.03762", "http://arxiv.org/abs/2005.14165v1", "ARXIV:2203.02155"]
    out = semantic_scholar._prefix_arxiv_ids(inputs)
    assert out == ["ARXIV:1706.03762", "ARXIV:2005.14165", "ARXIV:2203.02155"]
