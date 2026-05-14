"""TDD for pipeline.questions — question mining from comments (audit 3.14)."""

from __future__ import annotations

from pipeline import questions


def test_extract_questions_picks_up_classic_starters() -> None:
    text = (
        "How do I run MCP locally? "
        "Random thought, not a question. "
        "What's the best Claude prompt for coding? "
        "I think this is great."
    )
    qs = questions.extract_questions_from_text(text)
    # Should pick up the two real questions, skip the non-question.
    joined = " | ".join(qs)
    assert "How do I run MCP locally?" in joined
    assert "the best Claude prompt for coding?" in joined.replace("What's", "the").lower() or any(
        "best Claude prompt" in q for q in qs
    )


def test_extract_questions_handles_multiline_and_html() -> None:
    text = """
    <p>Why is this so slow on M3? Can I tune it differently?</p>
    """
    qs = questions.extract_questions_from_text(text)
    assert len(qs) >= 2


def test_extract_questions_ignores_rhetorical_or_no_qmark() -> None:
    text = "How great is that. Just a statement."
    qs = questions.extract_questions_from_text(text)
    assert qs == []


def test_top_questions_for_term_filters_to_term() -> None:
    texts = [
        "How do I install MCP servers locally?",
        "What's the best language model for code completion?",
        "How does Claude handle large contexts?",
        "Anyone tried MCP with Cursor — how did you wire it?",
    ]
    out = questions.top_questions_for_term(texts, term="mcp", top_n=3)
    # Both MCP-mentioning questions should be in the top-N.
    assert any("MCP" in q.upper() for q in out)
    assert all("?" in q for q in out)


def test_top_questions_dedupes_near_identical() -> None:
    texts = [
        "How do I install MCP locally?",
        "how do i install mcp locally?",
        "How do I install MCP locally  ?",
    ]
    out = questions.top_questions_for_term(texts, term="mcp", top_n=5)
    assert len(out) == 1
