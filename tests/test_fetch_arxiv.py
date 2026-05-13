"""TDD for pipeline.fetch.arxiv against an offline Atom feed fixture."""

from datetime import datetime
from pathlib import Path

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
