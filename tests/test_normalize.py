"""TDD for pipeline.normalize — n-gram extraction + canonicalize + aliases."""

from datetime import datetime, timezone

from pipeline import normalize
from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost


def _paper(title: str, abstract: str = "") -> Paper:
    return Paper(
        id=f"http://arxiv.org/abs/{abs(hash(title)) % 10_000_000}",
        title=title,
        abstract=abstract or "abstract body",
        authors=["alice"],
        published_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        primary_category="cs.AI",
        url="http://example.com",
    )


def _post(title: str, story_text: str = "") -> HNPost:
    return HNPost(
        id=abs(hash(title)) % 10_000_000,
        title=title,
        url="http://example.com",
        points=20,
        num_comments=10,
        created_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        story_text=story_text,
        author="alice",
    )


def _repo(full_name: str, description: str = "", topics: list[str] | None = None) -> RepoStat:
    return RepoStat(
        full_name=full_name,
        description=description,
        stars=100,
        topics=topics or ["ai"],
        created_at=datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc),
        pushed_at=datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc),
        html_url="http://example.com/repo",
    )


def _canonical_set(terms: list[normalize.Term]) -> set[str]:
    return {t.canonical_form for t in terms}


def test_two_char_ai_acronyms_survive_filter() -> None:
    terms = normalize.extract_candidate_terms([_paper("AI for Everyone")], [], [])
    assert "ai" in _canonical_set(terms), "lost 'ai' to the <3 char rule the spec wrote naïvely"


def test_canonical_form_is_lowercased_and_hyphenated() -> None:
    terms = normalize.extract_candidate_terms([_paper("Browser Agents at Scale")], [], [])
    canonicals = _canonical_set(terms)
    assert "browser-agents" in canonicals


def test_aliases_collapse_variants_to_canonical() -> None:
    terms = normalize.extract_candidate_terms(
        [_paper("Benchmarking gpt4o on hard tasks"), _paper("Probing gpt-4o reasoning")],
        [],
        [],
        aliases={"gpt4o": "gpt-4o"},
    )
    gpt = next((t for t in terms if t.canonical_form == "gpt-4o"), None)
    assert gpt is not None
    assert gpt.arxiv_mentions >= 2


def test_per_source_mention_counts_attribute_correctly() -> None:
    papers = [_paper("MCP servers everywhere"), _paper("Show me MCP done right"), _paper("MCP day")]
    posts = [_post("MCP for Claude")]
    repos = [_repo("acme/mcp-tools", description="MCP client")]
    terms = normalize.extract_candidate_terms(papers, posts, repos)
    mcp = next((t for t in terms if t.canonical_form == "mcp"), None)
    assert mcp is not None, "expected 'mcp' as a unigram"
    assert mcp.arxiv_mentions == 3
    assert mcp.hn_mentions == 1
    assert mcp.github_mentions >= 1


def test_stopwords_dropped() -> None:
    terms = normalize.extract_candidate_terms([_paper("The Survey of Methods")], [], [])
    canonicals = _canonical_set(terms)
    assert "the" not in canonicals
    assert "of" not in canonicals


def test_numeric_only_tokens_dropped() -> None:
    terms = normalize.extract_candidate_terms([_paper("Top 100 Benchmarks 2025")], [], [])
    canonicals = _canonical_set(terms)
    assert "100" not in canonicals
    assert "2025" not in canonicals


def test_hn_story_html_stripped_before_tokenization() -> None:
    posts = [_post("Show HN", story_text="<p>About <em>world models</em> and agents</p>")]
    terms = normalize.extract_candidate_terms([], posts, [])
    canonicals = _canonical_set(terms)
    # HTML tags shouldn't appear as terms
    assert "" not in canonicals
    assert all(not t.startswith("<") for t in canonicals)
    # The actual content should
    assert any("world" in c for c in canonicals)


def test_empty_inputs_return_empty() -> None:
    assert normalize.extract_candidate_terms([], [], []) == []


def test_raw_forms_preserve_observed_spellings_via_alias() -> None:
    terms = normalize.extract_candidate_terms(
        [_paper("gpt4o and gpt-4o together"), _paper("gpt4o again")],
        [],
        [],
        aliases={"gpt4o": "gpt-4o"},
    )
    target = next((t for t in terms if t.canonical_form == "gpt-4o"), None)
    assert target is not None
    # both spellings should appear as raw_forms (the alias source + the direct hit)
    assert "gpt4o" in target.raw_forms
