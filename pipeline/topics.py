"""Claude-driven topic extraction — the trend primitive for v0.1.1.

Per BACKEND_BUILD §7 Step 6+ (v0.1.1 amendment). Before this stage existed
the pipeline scored raw n-grams as trends, so the leaderboard surfaced
arxiv-abstract verbs ("propose", "framework", "tasks") and HTML escape
artifacts ("x27", "x2f"). This module consolidates real AI topics out of
the raw fetcher output in ONE Claude call per snapshot.

The system prompt is locked verbatim and cached ephemeral so repeat calls
within an hour hit the prompt cache.

Latency is irrelevant in a daily cron — one synchronous call wins on
simplicity over Batch API plumbing.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import anthropic
from pydantic import BaseModel

from pipeline.fetch.arxiv import Paper
from pipeline.fetch.github import RepoStat
from pipeline.fetch.hackernews import HNPost
from pipeline.log import log

HAIKU_MODEL = "claude-haiku-4-5"
# A real 30-50 topic response is ~14K chars ≈ 4K tokens. 8K gives ~2x
# headroom and still costs ~$0.032 worst-case on Haiku 4.5 output.
MAX_OUTPUT_TOKENS = 8000

ABSTRACT_PREVIEW_CHARS = 400
DESCRIPTION_PREVIEW_CHARS = 200
MAX_ARXIV_PAPERS_IN_PROMPT = 60
# Raised from 80 → 200 so the full HN fetch (~180 posts/run) reaches the
# topic extractor. The previous cap silently dropped ~55% of HN posts
# before Claude saw them, which is why hn_posts_7d was 0 on most trends.
# MAX_USER_PROMPT_CHARS still caps the whole prompt — if the HN block
# pushes us past the char budget, the truncation marker fires explicitly
# rather than silently slicing posts off the head.
MAX_HN_POSTS_IN_PROMPT = 200
MAX_GITHUB_REPOS_IN_PROMPT = 80
MAX_CANDIDATE_HINTS_IN_PROMPT = 200
MAX_USER_PROMPT_CHARS = 60_000
ANTHROPIC_TIMEOUT_SECONDS = 90.0

# Verbatim system prompt — DO NOT EDIT. Cached ephemeral so the daily
# cron's repeat calls (and any one-card reruns within the hour) hit the
# prompt cache.
SYSTEM_PROMPT = """You are a research-trend extractor for an AI alpha radar. You will receive:
- A list of arXiv paper titles + abstracts from the last 48 hours
- A list of Hacker News post titles + URLs from the last 7 days
- A list of GitHub repo names + descriptions created in the last 7 days
- A list of candidate n-gram hints surfaced by upstream normalization

Your job: extract the 30–50 most distinct AI research/builder TOPICS being discussed across these documents. A topic is a concrete technical concept, technique, model family, or research direction — NOT a generic abstract noun. Examples of valid topics: "world model agents", "test-time training", "diffusion language models", "browser-use agents (MCP)", "small reasoning models". Examples of INVALID topics: "framework", "proposed method", "performance", "experiments", "tasks".

For each topic, return:
- canonical_name: 2–5 word noun phrase, lowercased except for acronyms and proper nouns
- canonical_form: the canonical_name lowercased and hyphenated
- aliases: 0–3 alternative phrasings or acronyms (e.g. "MoE" is an alias of "mixture-of-experts routing")
- description: ONE plain-English sentence describing the topic, max 22 words, no jargon
- arxiv_ids: list of arXiv paper IDs (from the input) that mention this topic
- hn_post_ids: list of HN post IDs that mention this topic
- github_repos: list of GitHub repo full_names that mention this topic

Rules:
- A topic must be mentioned in at least 2 source documents OR by at least 2 distinct sources (e.g. 1 arXiv + 1 HN). Drop singletons.
- Prefer specificity. "agentic memory architectures" beats "memory".
- Merge near-duplicates. "world models" and "world model agents" collapse to one topic with both as aliases.
- Do not invent topics not grounded in the source documents.
- Return ONLY valid JSON matching the schema. No prose, no markdown fences.

Structured-output schema:
{
  "topics": [
    {
      "canonical_name": "string",
      "canonical_form": "string",
      "aliases": ["string"],
      "description": "string",
      "arxiv_ids": ["string"],
      "hn_post_ids": [123],
      "github_repos": ["owner/repo"]
    }
  ]
}"""

class ClaudeParseError(ValueError):
    """Raised when Claude's response can't be parsed as JSON."""


class Topic(BaseModel):
    """Topic extracted by Claude. source_doc_ids is keyed by SourceName
    (arxiv / hackernews / github), empty source lists omitted.
    """

    canonical_name: str
    canonical_form: str
    aliases: list[str] = []
    description: str
    source_doc_ids: dict[str, list[str | int]] = {}


def _extract_json(text: str) -> dict[str, Any]:
    """Parse JSON, stripping markdown fences if present.

    Tolerates: opening ``` (with or without `json` tag), closing ```,
    one-sided fences (Claude sometimes opens but doesn't close), and the
    bare-JSON case. Anything past the trailing `}` is discarded.
    """
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeParseError(f"Claude response was not valid JSON: {text[:120]!r}") from e


def _format_arxiv_block(papers: list[Paper]) -> str:
    if not papers:
        return "(no arXiv papers in the last 48 hours)"
    lines = []
    for p in papers:
        abstract = p.abstract.replace("\n", " ").strip()
        if len(abstract) > ABSTRACT_PREVIEW_CHARS:
            abstract = abstract[:ABSTRACT_PREVIEW_CHARS].rstrip() + "..."
        lines.append(f"- ID: {p.id} | Title: {p.title}\n  Abstract: {abstract}")
    return "\n".join(lines)


def _format_hn_block(posts: list[HNPost]) -> str:
    if not posts:
        return "(no Hacker News posts in the last 7 days)"
    lines = []
    for p in posts:
        url = p.url or ""
        lines.append(f"- ID: {p.id} | Title: {p.title} | URL: {url}")
    return "\n".join(lines)


def _format_github_block(repos: list[RepoStat]) -> str:
    if not repos:
        return "(no GitHub repos created in the last 7 days)"
    lines = []
    for r in repos:
        desc = (r.description or "").replace("\n", " ").strip()
        if len(desc) > DESCRIPTION_PREVIEW_CHARS:
            desc = desc[:DESCRIPTION_PREVIEW_CHARS].rstrip() + "..."
        lines.append(f"- repo: {r.full_name}\n  Description: {desc}")
    return "\n".join(lines)


def _format_hints_block(hints: list[str]) -> str:
    if not hints:
        return "(no upstream hints)"
    return ", ".join(hints)


def _dedupe_nonempty(values: list[str], *, limit: int) -> tuple[list[str], int]:
    seen: set[str] = set()
    out: list[str] = []
    skipped = 0
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if len(out) < limit:
            out.append(cleaned)
        else:
            skipped += 1
    return out, skipped


def _truncate_prompt(prompt: str) -> str:
    if len(prompt) <= MAX_USER_PROMPT_CHARS:
        return prompt
    marker = "\n\n[truncated to fit topic-extraction input budget]"
    return prompt[: MAX_USER_PROMPT_CHARS - len(marker)].rstrip() + marker


def _build_user_prompt(
    papers: list[Paper],
    posts: list[HNPost],
    repos: list[RepoStat],
    candidate_hints: list[str],
) -> str:
    prompt_papers = papers[:MAX_ARXIV_PAPERS_IN_PROMPT]
    prompt_posts = posts[:MAX_HN_POSTS_IN_PROMPT]
    prompt_repos = repos[:MAX_GITHUB_REPOS_IN_PROMPT]
    prompt_hints, omitted_hints = _dedupe_nonempty(
        candidate_hints, limit=MAX_CANDIDATE_HINTS_IN_PROMPT
    )
    omitted = {
        "arxiv_papers": max(0, len(papers) - len(prompt_papers)),
        "hackernews_posts": max(0, len(posts) - len(prompt_posts)),
        "github_repos": max(0, len(repos) - len(prompt_repos)),
        "candidate_hints": omitted_hints,
    }
    budget_note = (
        "Input budget note: "
        f"omitted={json.dumps(omitted, sort_keys=True)}"
    )
    prompt = (
        "arXiv papers (last 48 hours):\n"
        f"{_format_arxiv_block(prompt_papers)}\n\n"
        "Hacker News posts (last 7 days):\n"
        f"{_format_hn_block(prompt_posts)}\n\n"
        "GitHub repos (last 7 days):\n"
        f"{_format_github_block(prompt_repos)}\n\n"
        "Candidate n-gram hints from upstream normalization:\n"
        f"{_format_hints_block(prompt_hints)}\n\n"
        f"{budget_note}"
    )
    return _truncate_prompt(prompt)


def _system_block() -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _parse_topic_entry(entry: dict[str, Any]) -> Topic:
    source_doc_ids: dict[str, list[str | int]] = {}
    arxiv_ids = entry.get("arxiv_ids") or []
    hn_ids = entry.get("hn_post_ids") or []
    gh_repos = entry.get("github_repos") or []
    if arxiv_ids:
        source_doc_ids["arxiv"] = list(arxiv_ids)
    if hn_ids:
        source_doc_ids["hackernews"] = list(hn_ids)
    if gh_repos:
        source_doc_ids["github"] = list(gh_repos)
    return Topic(
        canonical_name=entry["canonical_name"],
        canonical_form=entry["canonical_form"],
        aliases=list(entry.get("aliases") or []),
        description=entry["description"],
        source_doc_ids=source_doc_ids,
    )


def extract_topics(
    papers: list[Paper],
    posts: list[HNPost],
    repos: list[RepoStat],
    candidate_hints: list[str],
    *,
    client: Optional[anthropic.Anthropic] = None,
) -> list[Topic]:
    """One Claude call → 30–50 named topics with source-doc attribution.

    Short-circuits on empty inputs (no documents AND no hints) — saves a
    pointless Claude call when the day's fetchers all bailed.
    """
    if not papers and not posts and not repos and not candidate_hints:
        return []

    if client is None:
        client = anthropic.Anthropic(timeout=ANTHROPIC_TIMEOUT_SECONDS)

    user_prompt = _build_user_prompt(papers, posts, repos, candidate_hints)
    log(
        "topic_extraction_start",
        model=HAIKU_MODEL,
        prompt_chars=len(user_prompt),
        arxiv_in=min(len(papers), MAX_ARXIV_PAPERS_IN_PROMPT),
        hn_in=min(len(posts), MAX_HN_POSTS_IN_PROMPT),
        github_in=min(len(repos), MAX_GITHUB_REPOS_IN_PROMPT),
        hints_in=min(len({h.strip() for h in candidate_hints if h.strip()}), MAX_CANDIDATE_HINTS_IN_PROMPT),
    )

    with client.messages.stream(
        model=HAIKU_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=_system_block(),
        messages=[
            {"role": "user", "content": user_prompt}
        ],
    ) as stream:
        chunks = 0
        for _ in stream.text_stream:
            chunks += 1
            if chunks % 50 == 0:
                log("topic_extraction_progress", chunks=chunks)
        final = stream.get_final_message()

    if getattr(final, "stop_reason", None) == "max_tokens":
        raise ClaudeParseError(
            "Claude hit max_tokens mid-output — JSON is truncated. "
            f"Increase MAX_OUTPUT_TOKENS (current: {MAX_OUTPUT_TOKENS})."
        )
    parsed = _extract_json(final.content[0].text)
    entries = parsed.get("topics") or []
    log("topic_extraction_done", model=HAIKU_MODEL, topics=len(entries))
    return [_parse_topic_entry(e) for e in entries]
