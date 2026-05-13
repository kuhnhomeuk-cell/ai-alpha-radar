"""Demand-cluster mining — the unique product wedge.

Per BACKEND_BUILD §7 Step 11. For each tracked trend, scrape comments
mentioning the trend's keyword and ask Sonnet 4.6 to identify the top 3
question-shapes: recurring asks, pain points, unmet needs.

Day-1 ships HN-only comment sources. Week 4 adds YouTube (VidIQ MCP),
Reddit (OAuth proxy), and X replies (Grok). The orchestrator and the
DemandCluster schema already accept the wider source set — we just
don't fetch them yet.

Dedup across trends uses MiniLM cosine similarity > 0.85 on
question_shape embeddings: two trends often produce overlapping demand
clusters, and the dashboard wants distinct rows.
"""

from __future__ import annotations

from typing import Any, Optional

import anthropic
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from pipeline.cluster import _get_model
from pipeline.fetch.hackernews import HNComment, HNPost
from pipeline.models import DemandCluster, DemandQuote
from pipeline.summarize import (
    MAX_OUTPUT_TOKENS_BRIEFING,
    SONNET_MODEL,
    _extract_json,
    _system_block,
)

DEFAULT_NICHE = "AI tools for solo creators"
MIN_COMMENTS_FOR_CLAUDE = 3
MAX_COMMENTS_PER_CALL = 20
DEFAULT_DEDUPE_THRESHOLD = 0.85

DEMAND_CLUSTER_PROMPT_TEMPLATE = (
    "Given these {n} comments about \"{keyword}\", identify the top 3 "
    "question-shapes - recurring questions, pain points, or unmet needs. "
    "For each, return:\n"
    "- question_shape (canonical form of the question)\n"
    "- askers_estimate (how many comments express this - be conservative)\n"
    "- quotes: 2-3 verbatim representative quotes with their source citation\n"
    "- weekly_growth_pct (estimate based on the comment timestamps provided)\n"
    "- open_window_days (your estimate of how long until someone fills the void)\n"
    "- creator_brief: 2-3 sentence content brief for a creator who wants to answer this\n\n"
    "Comments:\n{comments_block}\n\n"
    "Return ONLY valid JSON: a list of up to 3 question-shape objects."
)


def find_comments_for_keyword(
    keyword: str, posts: list[HNPost]
) -> list[tuple[HNComment, HNPost]]:
    """Pull comments from any post whose title or story_text contains the keyword.

    Posts without hydrated comments are skipped. Match is case-insensitive
    substring on the canonical/raw keyword string.
    """
    needle = keyword.lower()
    matched: list[tuple[HNComment, HNPost]] = []
    for post in posts:
        haystack = f"{post.title} {post.story_text or ''}".lower()
        if needle not in haystack:
            continue
        if not post.comments:
            continue
        for c in post.comments:
            matched.append((c, post))
    return matched


def _format_comments_block(
    comments: list[tuple[HNComment, HNPost]], *, limit: int
) -> str:
    lines = []
    for i, (c, p) in enumerate(comments[:limit]):
        title_snippet = p.title[:40]
        body = (c.text or "").strip().replace("\n", " ")[:300]
        lines.append(f"[{i + 1}] (HN - {title_snippet}) {body}")
    return "\n".join(lines)


def _coerce_cluster_list(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("clusters", "question_shapes", "results"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
    return []


def mine_demand_cluster(
    keyword: str,
    comments: list[tuple[HNComment, HNPost]],
    *,
    client: Optional[anthropic.Anthropic] = None,
    niche: str = DEFAULT_NICHE,
) -> list[DemandCluster]:
    """One Sonnet call producing 0-3 DemandClusters for a trend.

    Returns [] without calling Claude if comment count is below the floor —
    no point spending tokens on noise.
    """
    if len(comments) < MIN_COMMENTS_FOR_CLAUDE:
        return []
    if client is None:
        client = anthropic.Anthropic()

    block = _format_comments_block(comments, limit=MAX_COMMENTS_PER_CALL)
    prompt = DEMAND_CLUSTER_PROMPT_TEMPLATE.format(
        n=len(comments), keyword=keyword, comments_block=block
    )
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS_BRIEFING,
        system=_system_block(niche),
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = _extract_json(response.content[0].text)
    rows = _coerce_cluster_list(parsed)

    clusters: list[DemandCluster] = []
    for row in rows[:3]:
        if "question_shape" not in row:
            continue
        clusters.append(
            DemandCluster(
                question_shape=row["question_shape"],
                askers_estimate=int(row.get("askers_estimate") or 0),
                quotes=[
                    DemandQuote(
                        text=q.get("text", ""),
                        source=q.get("source", "HN"),
                    )
                    for q in row.get("quotes") or []
                ],
                sources=["hackernews"],
                weekly_growth_pct=float(row.get("weekly_growth_pct") or 0),
                open_window_days=int(row.get("open_window_days") or 14),
                creator_brief=row.get("creator_brief", ""),
                related_trends=[keyword],
            )
        )
    return clusters


def dedupe_clusters(
    clusters: list[DemandCluster], *, threshold: float = DEFAULT_DEDUPE_THRESHOLD
) -> list[DemandCluster]:
    """Greedy first-wins dedupe by question_shape embedding cosine similarity."""
    if len(clusters) <= 1:
        return list(clusters)

    model = _get_model()
    embeddings = np.asarray(
        model.encode([c.question_shape for c in clusters], show_progress_bar=False)
    )
    sims = cosine_similarity(embeddings)

    kept: list[int] = []
    for i in range(len(clusters)):
        if any(sims[i][j] > threshold for j in kept):
            continue
        kept.append(i)
    return [clusters[i] for i in kept]


def mine_demand_clusters_for_trends(
    keywords: list[str],
    posts: list[HNPost],
    *,
    max_clusters: int = 12,
    client: Optional[anthropic.Anthropic] = None,
    niche: str = DEFAULT_NICHE,
) -> list[DemandCluster]:
    """Run mine_demand_cluster across N trends, then dedupe + cap at max_clusters."""
    if client is None:
        client = anthropic.Anthropic()
    all_clusters: list[DemandCluster] = []
    for kw in keywords:
        comments = find_comments_for_keyword(kw, posts)
        all_clusters.extend(
            mine_demand_cluster(kw, comments, client=client, niche=niche)
        )
    deduped = dedupe_clusters(all_clusters)
    # Sort by askers_estimate desc — biggest demand surfaces first
    deduped.sort(key=lambda c: c.askers_estimate, reverse=True)
    return deduped[:max_clusters]
