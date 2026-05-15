"""TDD for pipeline.fetch.arxiv against an offline Atom feed fixture."""

from datetime import datetime
from pathlib import Path

import httpx
import pytest

from pipeline.fetch import arxiv

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "arxiv_sample.xml"
TARGET_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]


def _papers() -> list[arxiv.Paper]:
    return arxiv.parse_atom_feed(FIXTURE.read_text(encoding="utf-8"), categories=TARGET_CATEGORIES)


def test_parse_atom_feed_returns_at_least_ten_papers() -> None:
    papers = _papers()
    assert len(papers) >= 10


def test_every_paper_has_required_fields() -> None:
    for p in _papers():
        assert p.id, f"empty id: {p}"
        assert p.title.strip(), f"empty title: {p}"
        assert p.abstract.strip(), f"empty abstract: {p}"
        assert p.url.startswith("http"), f"bad url: {p.url}"
        assert isinstance(p.published_at, datetime)
        assert p.published_at.tzinfo is not None


def test_papers_filtered_by_primary_category() -> None:
    for p in _papers():
        assert p.primary_category in TARGET_CATEGORIES, (
            f"unexpected primary_category {p.primary_category} - filter regression"
        )


def test_authors_parsed_as_non_empty_strings() -> None:
    papers = _papers()
    assert any(p.authors for p in papers), "no paper had any author parsed"
    for p in papers:
        for a in p.authors:
            assert isinstance(a, str) and a.strip(), f"bad author entry: {a!r}"


def test_unknown_category_filter_returns_empty() -> None:
    papers = arxiv.parse_atom_feed(
        FIXTURE.read_text(encoding="utf-8"), categories=["does.notexist"]
    )
    assert papers == []


# v0.2.0 — new field extraction + backoff


def test_parse_atom_feed_extracts_comment() -> None:
    """arxiv:comment field is populated when present (fixture has "ICML2026")."""
    papers = _papers()
    comments = [p.comment for p in papers if p.comment]
    assert comments, "expected at least one paper to have a non-empty comment"
    assert any(
        arxiv.feedparser.parse  # sanity: feedparser still imported
        and any(v in c for v in ("ICML", "NeurIPS", "ICLR", "Work in Progress"))
        for c in comments
    ), f"expected a venue or status comment, got {comments[:5]!r}"


def test_parse_atom_feed_includes_primary_in_all_categories() -> None:
    """all_categories must always include the primary_category."""
    for p in _papers():
        assert p.primary_category in p.all_categories, (
            f"{p.id}: primary {p.primary_category!r} missing from {p.all_categories}"
        )


def test_fetch_with_backoff_retries_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run of 429s exhausts retries and raises ArXivRateLimited."""
    call_count = 0

    def fake_get(self, url, params=None):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(429, request=request)
        return response

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    monkeypatch.setattr(arxiv.time, "sleep", lambda _: None)

    with httpx.Client() as client:
        with pytest.raises(arxiv.ArXivRateLimited):
            arxiv._fetch_with_backoff(client, arxiv.ARXIV_API_URL, {}, max_retries=3)

    assert call_count == 3, f"expected 3 attempts, got {call_count}"
