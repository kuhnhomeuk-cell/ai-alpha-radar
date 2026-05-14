"""TDD for pipeline.fetch.producthunt — GraphQL v2 launches (audit 3.4)."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.fetch import producthunt

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "producthunt_sample.json"


def _load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_response_extracts_launches() -> None:
    launches = producthunt.parse_response(_load())
    assert len(launches) == 3
    by_name = {l.name: l for l in launches}
    assert "Claude Coder" in by_name
    assert by_name["Claude Coder"].votes_count == 540
    assert "Developer Tools" in by_name["Claude Coder"].topics


def test_filter_creator_relevant_keeps_ai_topics() -> None:
    launches = producthunt.parse_response(_load())
    creator = producthunt.filter_creator_relevant(launches)
    names = {l.name for l in creator}
    assert "Claude Coder" in names
    assert "FlowDiff" in names
    # NoteCanvas has only "Productivity" — keep it because it's in the allow list.
    assert "NoteCanvas" in names


def test_launches_per_term_counts_substring_matches() -> None:
    launches = producthunt.parse_response(_load())
    counts = producthunt.launches_per_term(launches, terms=["claude", "diffusion"])
    assert counts["claude"] == 1
    assert counts["diffusion"] == 1


def test_empty_response_returns_empty_list() -> None:
    assert producthunt.parse_response({}) == []
    assert producthunt.parse_response({"data": {}}) == []
