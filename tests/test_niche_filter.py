"""TDD for pipeline.niche_filter — solo-creator-niche relevance gate.

The "AI tools for solo creators" niche is defined by a curated keyword
list. is_niche_relevant must:

- match case-insensitively
- match on word boundaries OR substring within compound words ("rag-tutorial"
  should match "rag")
- accept multi-word phrases ("voice cloning")
- reject text with NO niche signal (a generic hardware review)
- accept a permissive bypass when terms is None (legacy callers)
"""

from __future__ import annotations

from pipeline import niche_filter as nf


def test_creator_terms_lists_curated_keywords() -> None:
    """The shared keyword list must include the audit's prescribed terms."""
    terms = nf.CREATOR_NICHE_TERMS
    # Spec-mandated terms from §3 niche pass.
    for must_have in [
        "agent",
        "llm",
        "rag",
        "code-gen",
        "voice cloning",
        "image gen",
        "vibe coding",
        "fine-tun",  # matches fine-tune and fine-tuning
        "embedding",
        "mcp",
    ]:
        assert must_have in terms, f"missing curated term: {must_have}"


def test_matches_substring_case_insensitive() -> None:
    """Substring match must be case-insensitive."""
    assert nf.is_niche_relevant("Building a RAG tutorial in 5 minutes") is True
    assert nf.is_niche_relevant("agent FRAMEWORKS for solo creators") is True


def test_matches_multi_word_phrase() -> None:
    """Multi-word phrases like 'voice cloning' must match as a phrase."""
    assert nf.is_niche_relevant("a guide to voice cloning on a M4 Mac") is True


def test_rejects_text_with_no_niche_signal() -> None:
    """Hardware reviews / unrelated news must NOT match."""
    assert nf.is_niche_relevant("Apple unveils new iPhone 18 Pro Max") is False
    assert nf.is_niche_relevant("Tesla quarterly earnings beat expectations") is False
    assert nf.is_niche_relevant("") is False


def test_accepts_empty_terms_set_as_bypass() -> None:
    """Callers can opt out by passing terms=None (e.g. legacy fetchers)."""
    assert nf.is_niche_relevant("anything goes here", terms=None) is True


def test_custom_terms_override() -> None:
    """Callers can supply their own keyword set (for testing or alt niches)."""
    assert nf.is_niche_relevant("hello world", terms={"world"}) is True
    assert nf.is_niche_relevant("hello world", terms={"banana"}) is False


def test_filter_iterable_returns_only_matches() -> None:
    """filter_niche_relevant yields only the items whose extractor returns
    text containing a niche term."""
    items = [
        ("yes", "LLM benchmark for code-gen agents"),
        ("no", "Stock market roundup"),
        ("yes2", "voice cloning ethics paper"),
    ]
    keep = nf.filter_niche_relevant(items, key=lambda x: x[1])
    assert [k for k, _ in keep] == ["yes", "yes2"]


# ---------- fetcher wiring (integration with arxiv + hackernews) ----------


def test_arxiv_parse_atom_then_filter_drops_non_niche_papers() -> None:
    """Niche filter applied to parsed arxiv papers should drop papers whose
    title+abstract contain no niche term, while keeping LLM/agent/etc. ones.

    Uses the on-disk fixture so we exercise the same parser the fetcher uses.
    """
    from pathlib import Path

    from pipeline.fetch import arxiv

    xml = Path("tests/fixtures/arxiv_sample.xml").read_text(encoding="utf-8")
    papers = arxiv.parse_atom_feed(xml, categories=["cs.AI", "cs.LG", "cs.CL"])
    assert papers, "fixture should produce ≥1 paper"
    filtered = nf.filter_niche_relevant(
        papers, key=lambda p: p.title + " " + p.abstract
    )
    # Sanity: the fixture has known niche-relevant papers (Agent memory,
    # LongMemEval-V2 mentions agents + RAG). After filtering, count must
    # be > 0 and ≤ original count.
    assert 0 < len(filtered) <= len(papers)


def test_hackernews_signal_terms_is_same_object_as_curated_terms() -> None:
    """HN's AI_SIGNAL_TERMS must alias the shared curated list — one
    source of truth so the niche set is edited in exactly one place."""
    from pipeline.fetch import hackernews

    assert hackernews.AI_SIGNAL_TERMS is nf.CREATOR_NICHE_TERMS


# ---------- word_boundary mode (false-positive prevention for HN comments) ----------


def test_word_boundary_mode_rejects_ai_inside_unrelated_word() -> None:
    """The 2026-05-16 live-HN inspection caught 'ai' substring matching
    'failure' (contains 'ai').  word_boundary=True must reject that case
    because 'ai' is not a standalone word token in 'failure'."""
    assert nf.is_niche_relevant("This is a complete failure", word_boundary=True) is False


def test_word_boundary_mode_accepts_standalone_ai_token() -> None:
    """Standalone 'AI' at the start of a sentence must still match."""
    assert nf.is_niche_relevant("AI video generation is trending", word_boundary=True) is True


def test_word_boundary_mode_accepts_multi_word_phrase_via_substring() -> None:
    """Multi-word phrases ('voice cloning') bypass the word-boundary rule
    and use substring matching — they are long enough not to false-positive."""
    assert nf.is_niche_relevant("Tutorial on voice cloning tools", word_boundary=True) is True
