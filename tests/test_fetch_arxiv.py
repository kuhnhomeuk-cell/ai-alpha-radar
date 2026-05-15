"""TDD for pipeline.fetch.arxiv against an offline Atom feed fixture."""

from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from pipeline.fetch import _retry, arxiv

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "arxiv_sample.xml"
TARGET_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL"]


@pytest.fixture
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eliminate retry/rate-limit sleeps so HTTP tests stay sub-second."""
    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
    monkeypatch.setattr(arxiv.time, "sleep", lambda _s: None)


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


# ---------- audit 4.5 — HTTP-layer integration tests ----------


@respx.mock
def test_fetch_recent_papers_200_path_parses_feed(_no_sleep: None) -> None:
    route = respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8")),
    )
    papers = arxiv.fetch_recent_papers(TARGET_CATEGORIES, lookback_days=0)
    assert route.called
    assert len(papers) >= 1
    assert all(p.primary_category in TARGET_CATEGORIES for p in papers)


@respx.mock
def test_fetch_recent_papers_honors_retry_after_then_succeeds(_no_sleep: None) -> None:
    body = FIXTURE.read_text(encoding="utf-8")
    route = respx.get("https://export.arxiv.org/api/query").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, text=body),
        ]
    )
    papers = arxiv.fetch_recent_papers(TARGET_CATEGORIES, lookback_days=0)
    assert route.call_count == 2  # one retry consumed
    assert len(papers) >= 1


@respx.mock
def test_fetch_recent_papers_500_exhausts_and_raises(_no_sleep: None) -> None:
    route = respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(500),
    )
    with pytest.raises(httpx.HTTPStatusError):
        arxiv.fetch_recent_papers(TARGET_CATEGORIES, lookback_days=0)
    assert route.call_count == 3  # 1 initial + 2 retries


@respx.mock
def test_fetch_recent_papers_malformed_body_returns_empty(_no_sleep: None) -> None:
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text="<not a real feed>"),
    )
    # feedparser tolerates garbage; we expect zero entries parsed, not a raise
    papers = arxiv.fetch_recent_papers(TARGET_CATEGORIES, lookback_days=0)
    assert papers == []


# ---------- v0.2.0 — comment + all_categories extraction ----------


def test_parse_atom_feed_extracts_comment_when_present() -> None:
    """arxiv:comment is populated for entries that have one
    (fixture contains 'ICML2026' and 'Work in Progress').
    """
    papers = _papers()
    comments = [p.comment for p in papers if p.comment]
    assert comments, "expected at least one paper with a non-empty comment"
    # At least one venue-acceptance or progress-state comment should appear.
    assert any(
        v in c for c in comments for v in ("ICML", "NeurIPS", "ICLR", "Work in Progress")
    ), f"expected a venue or status comment, got {comments[:5]!r}"


def test_parse_atom_feed_includes_primary_in_all_categories() -> None:
    """all_categories always contains primary_category plus cross-listed categories."""
    for p in _papers():
        assert p.primary_category in p.all_categories, (
            f"{p.id}: primary {p.primary_category!r} missing from {p.all_categories}"
        )
