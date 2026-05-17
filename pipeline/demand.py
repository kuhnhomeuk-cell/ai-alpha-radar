"""Demand-cluster mining — the unique product wedge.

Per BACKEND_BUILD §7 Step 11 + PRODUCT.md "demand signals" wedge.

Two architectures live side-by-side here:

1. **Legacy per-trend mining** (`mine_demand_cluster`, `mine_demand_clusters_for_trends`)
   — one Sonnet call per tracked trend, keyword-matched. Kept for backward
   compatibility with tests and any future single-trend uses.

2. **HDBSCAN comment clustering** (`mine_demand_clusters_from_comments`,
   Phase 3) — gather question-shaped, niche-relevant comments across all
   posts, embed with MiniLM, cluster with HDBSCAN, then run one Claude call
   per cluster via the Batch API (~50% cost). This is the path the
   orchestrator uses for the shipped snapshot.

The new path is the wedge. Karpathy §5: before any paid batch, we render
ONE cluster synchronously and inspect the output. The `sync_probe=True`
default enforces this in the orchestrator.

Day-1 ships HN-only. The wider source set (YouTube, Reddit, X) is wired
into the DemandCluster schema's `sources` field for week-4 expansion.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

import anthropic
import hdbscan
import numpy as np
from pydantic import BaseModel
from sklearn.metrics.pairwise import cosine_similarity

from pipeline.cluster import _get_model
from pipeline.fetch.hackernews import HNComment, HNPost
from pipeline.log import log
from pipeline.models import DemandCluster, DemandQuote
from pipeline.niche_filter import is_niche_relevant as _shared_is_niche_relevant
from pipeline.summarize import (
    BATCH_API_DISCOUNT,
    HAIKU_INPUT_DOLLARS_PER_MILLION_TOKENS,
    HAIKU_MODEL,
    HAIKU_OUTPUT_DOLLARS_PER_MILLION_TOKENS,
    SONNET_MODEL,
    _build_request_params,
    _extract_json,
    _submit_and_collect_batch,
    _system_block,
    ClaudeParseError,
)

DEFAULT_NICHE = "AI tools for solo creators"
MIN_COMMENTS_FOR_CLAUDE = 3
MAX_COMMENTS_PER_CALL = 20
DEFAULT_DEDUPE_THRESHOLD = 0.85

# Demand cluster outputs are bigger than the daily briefing — up to 3
# question_shape entries each with 2-3 verbatim quotes, a creator brief,
# plus the scalar fields. 600 tokens (the briefing budget) truncates the
# JSON mid-string on a typical legacy call. 1200 fits a full 3-cluster
# response with headroom; the new comment-clustering path emits ONE
# cluster per call so 800 would suffice but we use the same constant for
# both for cache-key parity.
MAX_OUTPUT_TOKENS_DEMAND = 1200

# Niche-relevance vocabulary for HN comment filtering. Differs from
# pipeline.niche_filter.CREATOR_NICHE_TERMS by design: that set is tuned
# for short structured text (paper/repo titles, substring-matched). This
# set is tuned for natural-language HN comments (word-boundary matched)
# and includes short tokens like "ai" / "api" / "ide" that would false-
# positive under substring matching. A keyword hit on the comment text
# itself qualifies — niche-relevant parent posts don't pull every reply.
NICHE_KEYWORDS_AI_TOOLS_FOR_SOLO_CREATORS: frozenset[str] = frozenset({
    # AI vocabulary
    "ai", "llm", "gpt", "claude", "gemini", "mistral", "llama", "anthropic",
    "openai", "agent", "agents", "mcp", "embedding", "embeddings", "rag",
    "fine-tun", "diffusion", "stable diffusion", "huggingface", "hugging face",
    "transformer", "inference", "prompt", "prompting", "model context",
    "haiku", "sonnet", "opus", "vidiq", "elevenlabs",
    # Tooling / dev
    "api", "sdk", "batch", "cache", "caching", "self-host", "self-hosted",
    "vibe code", "ide",
    # Creator
    "creator", "creators", "indie", "solo", "shorts", "youtube", "thumbnail",
    "thumbnails", "caption", "captions", "b-roll", "broll", "voiceover",
    "voice-over", "tts", "transcribe", "transcript", "channel", "video",
    "videos", "shorts script", "audience",
})

# Question-shape patterns. A comment is question-shaped if it matches any:
# - ends with '?'
# - starts with a known intent prefix
_QUESTION_INTENT_PREFIXES: tuple[str, ...] = (
    "how do i", "how do you", "how can i", "how can you", "how to",
    "what's the best", "whats the best", "what is the best",
    "anyone know", "anyone got", "anyone tried", "anyone using",
    "looking for", "is there", "are there", "any way to",
    "what's the right", "whats the right",
)

# Comments shorter than this are dropped — too little signal to cluster.
MIN_COMMENT_CHARS = 25
# Comments longer than this get truncated before embedding/Claude (cost
# control). The truncation only affects what gets sent downstream — the
# raw text is preserved on the HNComment object.
MAX_COMMENT_CHARS_FOR_PROMPT = 300

# HDBSCAN floor for comment-level clustering. Comments are sparser than
# topics; min_cluster_size=3 gives meaningful clusters without blowing up
# on the noise label.
HDBSCAN_MIN_CLUSTER_SIZE_COMMENTS = 3

# Per-cluster prompt — same JSON shape as the legacy per-trend prompt so
# the DemandCluster schema is reused 1:1.
CLUSTER_PROMPT_TEMPLATE = (
    "Below are {n} HN comments that all express related question-shaped "
    "intent in the niche \"{niche}\". Distill the cluster into ONE "
    "DemandCluster JSON object with these fields:\n"
    "- question_shape (single canonical question, <=18 words)\n"
    "- askers_estimate (integer, how many distinct askers — be conservative)\n"
    "- quotes: 2-3 verbatim representative quotes, each with "
    "{{\"text\": ..., \"source\": \"HN\"}}\n"
    "- weekly_growth_pct (rough estimate from the relative volume; default 10 if unclear)\n"
    "- open_window_days (integer 1-60: how long until someone fills this void)\n"
    "- creator_brief: 2-3 sentence content brief for a solo creator who wants to answer this\n\n"
    "Comments:\n{comments_block}\n\n"
    "Return ONLY valid JSON for one DemandCluster object. No prose, no markdown fences."
)

# Legacy per-trend prompt, kept verbatim for backward compatibility.
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


# ---------- New architecture: question-shape filter ----------


def is_question_shaped(text: str) -> bool:
    """True if `text` looks like a question or unmet-need ask.

    Two predicates (OR):
      - the trimmed text ends with '?'
      - the lowercased trimmed text starts with a known intent prefix
        ("how do I", "looking for", "anyone know", "what's the best", ...)
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.rstrip().endswith("?"):
        return True
    head = stripped.lower()
    return any(head.startswith(prefix) for prefix in _QUESTION_INTENT_PREFIXES)


# ---------- Niche filter: thin delegate to pipeline.niche_filter ----------


def is_niche_relevant(
    text: str, *, niche_keywords: frozenset[str] = NICHE_KEYWORDS_AI_TOOLS_FOR_SOLO_CREATORS
) -> bool:
    """Word-boundary aware niche test for HN comments.

    Delegates to pipeline.niche_filter.is_niche_relevant with
    word_boundary=True. The duplicated regex/split logic that lived here
    pre-2026-05-16 is now centralised in niche_filter — this wrapper
    preserves the comment-specific keyword default and call-site shape.
    """
    return _shared_is_niche_relevant(
        text, terms=niche_keywords, word_boundary=True
    )


# ---------- Comment gathering ----------


class _GatheredComment(BaseModel):
    """Convenience tuple wrapper for (comment, parent_post) pairs used by
    downstream stages. Plain tuples lose attribute readability in tests.
    """

    comment: HNComment
    post: HNPost

    model_config = {"arbitrary_types_allowed": True}


def gather_question_comments(
    posts: list[HNPost],
    *,
    niche_keywords: frozenset[str] = NICHE_KEYWORDS_AI_TOOLS_FOR_SOLO_CREATORS,
    max_age_days: int = 7,
    now: Optional[datetime] = None,
) -> list[tuple[HNComment, HNPost]]:
    """Walk every post → every comment, filter by question shape + niche +
    freshness. Returns a flat list of (comment, parent_post) tuples.

    A comment is kept iff:
      - has text and ≥ MIN_COMMENT_CHARS
      - `is_question_shaped(comment.text)` is True
      - the comment text itself hits a niche keyword (parent-post niche
        relevance alone isn't enough — that pulled too many sarcastic /
        off-niche asides through in live HN inspection on 2026-05-16)
      - `comment.created_at` is within `max_age_days` of `now`
    """
    if now is None:
        now = datetime.now(tz=timezone.utc)
    max_age = max_age_days
    gathered: list[tuple[HNComment, HNPost]] = []

    for post in posts:
        if not post.comments:
            continue
        for c in post.comments:
            text = (c.text or "").strip()
            if len(text) < MIN_COMMENT_CHARS:
                continue
            if not is_question_shaped(text):
                continue
            # Niche: the comment ITSELF must hit a niche keyword. A
            # niche-relevant parent post (e.g. "Claude for Small Business")
            # doesn't automatically qualify every reply under it — most
            # replies are off-niche asides.
            if not is_niche_relevant(text, niche_keywords=niche_keywords):
                continue
            # freshness
            try:
                age_days = (now - c.created_at).total_seconds() / 86400.0
            except (TypeError, ValueError):
                continue
            if age_days > max_age:
                continue
            gathered.append((c, post))
    return gathered


# ---------- HDBSCAN clustering ----------


class CommentCluster(BaseModel):
    """One HDBSCAN-derived cluster of question-shaped comments.

    `cluster_id == -1` is HDBSCAN's noise label and is excluded from the
    summarization stage upstream.
    """

    cluster_id: int
    comments: list[tuple[HNComment, HNPost]]

    model_config = {"arbitrary_types_allowed": True}


def cluster_comments_hdbscan(
    gathered: list[tuple[HNComment, HNPost]],
    *,
    min_cluster_size: int = HDBSCAN_MIN_CLUSTER_SIZE_COMMENTS,
) -> list[CommentCluster]:
    """Embed each comment with MiniLM, cluster with HDBSCAN (cosine-like).

    HDBSCAN doesn't natively use cosine distance; the convention in this
    repo (see `pipeline/cluster.py`) is to normalize embeddings and use
    euclidean — for unit vectors, euclidean is monotonic in cosine.
    """
    if len(gathered) < min_cluster_size:
        return []

    texts = [c.text[:MAX_COMMENT_CHARS_FOR_PROMPT] for c, _ in gathered]
    model = _get_model()
    embeddings = np.asarray(model.encode(texts, show_progress_bar=False))

    # Normalize so euclidean distance behaves like cosine.
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normalized = embeddings / norms

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size, metric="euclidean"
    )
    labels = clusterer.fit_predict(normalized)

    by_cluster: dict[int, list[tuple[HNComment, HNPost]]] = {}
    for (c, p), lbl in zip(gathered, labels):
        by_cluster.setdefault(int(lbl), []).append((c, p))

    return [
        CommentCluster(cluster_id=cid, comments=members)
        for cid, members in sorted(by_cluster.items())
        if cid != -1
    ]


# ---------- Per-cluster summarization (sync + batch) ----------


def _format_cluster_block(cluster: CommentCluster) -> str:
    lines: list[str] = []
    for i, (c, p) in enumerate(cluster.comments[:MAX_COMMENTS_PER_CALL]):
        title_snippet = p.title[:40]
        body = (c.text or "").strip().replace("\n", " ")[:MAX_COMMENT_CHARS_FOR_PROMPT]
        lines.append(f"[{i + 1}] (HN — {title_snippet}) {body}")
    return "\n".join(lines)


def _row_to_demand_cluster(
    row: dict[str, Any], *, related_keywords: list[str]
) -> Optional[DemandCluster]:
    """Coerce one Claude response dict into a DemandCluster.

    Returns None if a required field is missing — we never fabricate the
    wedge feature on garbage input.
    """
    if not isinstance(row, dict):
        return None
    qs = row.get("question_shape")
    if not isinstance(qs, str) or not qs.strip():
        return None
    quotes_raw = row.get("quotes") or []
    if not isinstance(quotes_raw, list):
        return None
    quotes = [
        DemandQuote(
            text=q.get("text", "")[:MAX_COMMENT_CHARS_FOR_PROMPT],
            source=q.get("source", "HN"),
        )
        for q in quotes_raw
        if isinstance(q, dict)
    ]
    try:
        askers = int(row.get("askers_estimate") or 0)
    except (TypeError, ValueError):
        askers = 0
    try:
        growth = float(row.get("weekly_growth_pct") or 0)
    except (TypeError, ValueError):
        growth = 0.0
    try:
        window = int(row.get("open_window_days") or 14)
    except (TypeError, ValueError):
        window = 14
    brief = (row.get("creator_brief") or "").strip()
    if not brief:
        return None
    return DemandCluster(
        question_shape=qs.strip(),
        askers_estimate=askers,
        quotes=quotes,
        sources=["hackernews"],
        weekly_growth_pct=growth,
        open_window_days=window,
        creator_brief=brief,
        related_trends=related_keywords,
    )


def _related_keywords_for_cluster(cluster: CommentCluster, *, max_n: int = 3) -> list[str]:
    """Extract the most-common AI/creator terms across the cluster's parent
    post titles. Used as the `related_trends` field. Falls back to a
    deduped list of parent-post titles (truncated) if no keywords hit.
    """
    counts: dict[str, int] = {}
    for c, p in cluster.comments:
        haystack = f"{p.title} {p.story_text or ''}".lower()
        for kw in NICHE_KEYWORDS_AI_TOOLS_FOR_SOLO_CREATORS:
            if kw in haystack:
                counts[kw] = counts.get(kw, 0) + 1
    if counts:
        return [
            kw for kw, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_n]
        ]
    seen: list[str] = []
    for _, p in cluster.comments:
        title = (p.title or "").strip()[:40]
        if title and title not in seen:
            seen.append(title)
        if len(seen) >= max_n:
            break
    return seen


def summarize_cluster_sync(
    cluster: CommentCluster,
    *,
    client: anthropic.Anthropic,
    niche: str = DEFAULT_NICHE,
    model: str = SONNET_MODEL,
) -> Optional[DemandCluster]:
    """Karpathy §5 sync probe — ONE non-batch Claude call, full price.

    Used before any paid batch to confirm the prompt + schema produce a
    usable DemandCluster. Returns None on parse failure rather than raising.
    """
    prompt = CLUSTER_PROMPT_TEMPLATE.format(
        n=len(cluster.comments),
        niche=niche,
        comments_block=_format_cluster_block(cluster),
    )
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS_DEMAND,
        system=_system_block(niche),
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    try:
        parsed = _extract_json(text)
    except ClaudeParseError:
        return None
    related = _related_keywords_for_cluster(cluster)
    return _row_to_demand_cluster(parsed, related_keywords=related)


def summarize_clusters_batch(
    clusters: list[CommentCluster],
    *,
    client: anthropic.Anthropic,
    niche: str = DEFAULT_NICHE,
    model: str = HAIKU_MODEL,
) -> list[DemandCluster]:
    """Batch API across all clusters — one request per cluster, ~50% cost
    vs sync. Each request's custom_id is `c{ordinal_index}` so the mapping
    back to a cluster is positional.
    """
    if not clusters:
        return []

    requests: list[dict[str, Any]] = []
    for i, cluster in enumerate(clusters):
        prompt = CLUSTER_PROMPT_TEMPLATE.format(
            n=len(cluster.comments),
            niche=niche,
            comments_block=_format_cluster_block(cluster),
        )
        requests.append(
            {
                "custom_id": f"c{i}",
                "params": _build_request_params(
                    niche=niche,
                    prompt=prompt,
                    model=model,
                    max_tokens=MAX_OUTPUT_TOKENS_DEMAND,
                ),
            }
        )

    results = _submit_and_collect_batch(client, requests)

    out: list[DemandCluster] = []
    for i, cluster in enumerate(clusters):
        parsed = results.get(f"c{i}")
        if parsed is None:
            continue
        related = _related_keywords_for_cluster(cluster)
        dc = _row_to_demand_cluster(parsed, related_keywords=related)
        if dc is not None:
            out.append(dc)
    return out


# ---------- Cost estimation ----------


def estimate_demand_batch_cost_cents(num_clusters: int) -> float:
    """Conservative cost estimate for one demand-clusters batch run.

    Uses Haiku pricing (the default model) and the 50% Batch API discount.
    Input budget: ~800 tokens per cluster (prompt + comment block).
    Output budget: MAX_OUTPUT_TOKENS_DEMAND.

    Tuned to surface the cap-trip risk in run.py before the batch fires —
    the orchestrator must add this to the existing Claude estimate.
    """
    if num_clusters <= 0:
        return 0.0
    input_tokens_per_req = 800
    output_tokens_per_req = MAX_OUTPUT_TOKENS_DEMAND
    total_input = num_clusters * input_tokens_per_req
    total_output = num_clusters * output_tokens_per_req
    dollars = (
        total_input / 1_000_000 * HAIKU_INPUT_DOLLARS_PER_MILLION_TOKENS
        + total_output / 1_000_000 * HAIKU_OUTPUT_DOLLARS_PER_MILLION_TOKENS
    )
    return dollars * BATCH_API_DISCOUNT * 100


# ---------- Top-level entry: comment-clustering pipeline ----------


def mine_demand_clusters_from_comments(
    posts: list[HNPost],
    *,
    client: Optional[anthropic.Anthropic] = None,
    niche: str = DEFAULT_NICHE,
    niche_keywords: frozenset[str] = NICHE_KEYWORDS_AI_TOOLS_FOR_SOLO_CREATORS,
    max_clusters: int = 12,
    max_age_days: int = 7,
    sync_probe: bool = True,
    batch_model: str = HAIKU_MODEL,
    probe_model: str = SONNET_MODEL,
    fallback_trend_keywords: Optional[list[str]] = None,
    min_clusters_for_skip_fallback: int = 4,
) -> list[DemandCluster]:
    """The wedge — end-to-end demand clustering from raw HN posts.

    Pipeline:
      1. gather_question_comments(...) — filter for question shape + niche + freshness
      2. cluster_comments_hdbscan(...) — MiniLM + HDBSCAN min_cluster_size=3
      3. if sync_probe: summarize_cluster_sync(...) on cluster[0] — the §5 probe
      4. summarize_clusters_batch(...) — Batch API across remaining clusters
      5. if total clusters < min_clusters_for_skip_fallback AND
         fallback_trend_keywords is non-empty: run the legacy per-trend
         path on those keywords to backfill. This catches the sparse-data
         days where HN hydration depth (top_n=10 by default) doesn't
         yield enough material to cluster — the wedge still ships.
      6. dedupe by cosine similarity > 0.85 on question_shape
      7. sort by askers_estimate desc, cap at max_clusters

    Returns [] when both paths are empty — keeps the snapshot valid.
    """
    if client is None:
        client = anthropic.Anthropic()

    gathered = gather_question_comments(
        posts, niche_keywords=niche_keywords, max_age_days=max_age_days
    )
    clusters: list[CommentCluster] = []
    if len(gathered) >= HDBSCAN_MIN_CLUSTER_SIZE_COMMENTS:
        clusters = cluster_comments_hdbscan(gathered)
        # HDBSCAN can return >12 on a noisy day; we prioritize by cluster
        # size (biggest demand surfaces first), over-fetch x2 to survive
        # per-cluster parse drops.
        clusters.sort(key=lambda c: len(c.comments), reverse=True)
        clusters = clusters[: max_clusters * 2]

    out: list[DemandCluster] = []
    if clusters:
        probed: Optional[DemandCluster] = None
        remaining = clusters
        if sync_probe:
            probed = summarize_cluster_sync(
                clusters[0], client=client, niche=niche, model=probe_model
            )
            remaining = clusters[1:]

        batched = summarize_clusters_batch(
            remaining, client=client, niche=niche, model=batch_model
        )

        if probed is not None:
            out.append(probed)
        out.extend(batched)

    # Sparse-day fallback: HN hydrates ~10 posts/day by default, which
    # often yields fewer than min_clusters_for_skip_fallback HDBSCAN
    # clusters. Backfill via the per-trend legacy path on the operator's
    # top trends — recall-over-precision when the wedge is otherwise empty.
    if (
        len(out) < min_clusters_for_skip_fallback
        and fallback_trend_keywords
    ):
        legacy = mine_demand_clusters_for_trends(
            fallback_trend_keywords,
            posts,
            client=client,
            niche=niche,
            max_clusters=max_clusters,
        )
        # Don't replace — extend. The HDBSCAN clusters (where they exist)
        # are higher precision, the legacy ones are recall fill.
        out.extend(legacy)

    out = dedupe_clusters(out)
    out.sort(key=lambda c: c.askers_estimate, reverse=True)
    return out[:max_clusters]


# ---------- Legacy per-trend pipeline (kept for backward compat) ----------


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


# Sonnet occasionally drifts from the prompt and returns rows keyed
# `question` or `pain_point` instead of the requested `question_shape`. Map
# the small, plausible-synonym set in rather than silently dropping the row
# (which produced a 0-cluster wedge in production). Keep this conservative —
# we don't want to coerce arbitrary keys.
_QUESTION_SHAPE_SYNONYMS: tuple[str, ...] = ("question", "pain_point", "prompt")


def _coerce_cluster_list(parsed: Any) -> list[dict[str, Any]]:
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        rows = []
        for key in ("clusters", "question_shapes", "results"):
            if key in parsed and isinstance(parsed[key], list):
                rows = parsed[key]
                break
    else:
        return []
    # In-place synonym remap: if a row lacks `question_shape` but carries a
    # known synonym, promote the synonym's value.
    for row in rows:
        if not isinstance(row, dict) or "question_shape" in row:
            continue
        for syn in _QUESTION_SHAPE_SYNONYMS:
            if syn in row and isinstance(row[syn], str) and row[syn].strip():
                row["question_shape"] = row[syn]
                break
    return rows


def mine_demand_cluster(
    keyword: str,
    comments: list[tuple[HNComment, HNPost]],
    *,
    client: Optional[anthropic.Anthropic] = None,
    niche: str = DEFAULT_NICHE,
) -> list[DemandCluster]:
    """Legacy: one Sonnet call producing 0-3 DemandClusters for a trend.

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
        max_tokens=MAX_OUTPUT_TOKENS_DEMAND,
        system=_system_block(niche),
        messages=[{"role": "user", "content": prompt}],
    )
    # Sonnet occasionally emits malformed JSON (literal quotes / newlines in
    # string fields). Treat that as "no clusters from this keyword" rather
    # than crashing the whole pipeline.
    try:
        parsed = _extract_json(response.content[0].text)
    except ClaudeParseError:
        import sys
        print(
            f"mine_demand_cluster: dropping cluster for '{keyword}' — Sonnet JSON unparseable",
            file=sys.stderr,
        )
        return []
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


# ---------- Ultimate fallback: synthesize demand from trends list ----------


SYNTHESIZE_PROMPT_TEMPLATE = (
    "You are a content-strategy analyst for a YouTube Shorts creator dashboard.\n"
    "Niche: \"{niche}\".\n\n"
    "Today's tracked trends (each line: keyword | summary | hook):\n"
    "{trends_block}\n\n"
    "Generate 6 distinct UNANSWERED creator questions ('demand clusters') that "
    "are emerging from these trends and would resonate with the niche audience.\n"
    "Each must be a question a solo creator's audience would ask but that no "
    "creator has definitively answered yet.\n\n"
    "Return ONLY a JSON array of 6 objects, each with these fields:\n"
    "- question_shape (canonical question form, <=18 words)\n"
    "- askers_estimate (integer 5-30, your honest read of audience demand)\n"
    "- weekly_growth_pct (integer estimate of weekly question-volume growth)\n"
    "- open_window_days (integer 7-45, how long until this question is saturated)\n"
    "- creator_brief (2 sentences of how a creator should answer this, niche-tailored)\n"
    "- related_trends (1-3 of the keywords above that this question connects to)\n\n"
    "No prose. No markdown. Just the JSON array."
)


def synthesize_demand_from_trends(
    trends: list[Any],  # list[Trend] but avoiding circular import
    *,
    client: Optional[anthropic.Anthropic] = None,
    niche: str = DEFAULT_NICHE,
    max_clusters: int = 6,
    model: str = SONNET_MODEL,
) -> list[DemandCluster]:
    """One-shot Sonnet call: generate demand clusters directly from the trend
    list, with zero HN dependency.

    This is the ultimate fallback for the wedge — when HDBSCAN gathering
    yields too few niche-relevant question comments AND the per-keyword
    legacy path finds zero matching HN posts (typical for abstract topic
    terms like 'speculative decoding' that don't literally appear in HN
    titles), we still ship 6 plausible creator questions.

    Distinct from the HN-driven paths: the wedge here is generated, not
    mined. Sources list reflects that ('inferred').
    """
    if not trends:
        return []
    if client is None:
        client = anthropic.Anthropic()

    trends_block_lines: list[str] = []
    for t in trends[:15]:
        summary = (getattr(t, "summary", "") or "")[:120]
        angles = getattr(t, "angles", None)
        hook = (getattr(angles, "hook", "") if angles else "") or ""
        hook = hook[:80]
        kw = getattr(t, "keyword", "") or ""
        trends_block_lines.append(f"- {kw} | {summary} | {hook}")
    trends_block = "\n".join(trends_block_lines)
    prompt = SYNTHESIZE_PROMPT_TEMPLATE.format(niche=niche, trends_block=trends_block)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS_DEMAND,
            system=_system_block(niche),
            messages=[{"role": "user", "content": prompt}],
        )
        parsed = _extract_json(response.content[0].text)
    except (ClaudeParseError, Exception) as exc:
        import sys
        print(
            f"synthesize_demand_from_trends: failed to parse Sonnet response — {exc}",
            file=sys.stderr,
        )
        return []

    rows = _coerce_cluster_list(parsed)
    # Drift diagnostic: log how many rows Sonnet returned and the shape of
    # the first row so a future key drift (question_shape → pain_point etc.)
    # is visible in the GitHub Actions log instead of silently producing []
    # downstream.
    log(
        "demand_synthesize_parsed",
        level="info",
        row_count=len(rows),
        first_row_keys=(
            list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        ),
    )
    if not rows:
        return []

    out: list[DemandCluster] = []
    for row in rows[:max_clusters]:
        if not isinstance(row, dict) or "question_shape" not in row:
            continue
        try:
            out.append(
                DemandCluster(
                    question_shape=row["question_shape"],
                    askers_estimate=int(row.get("askers_estimate") or 0),
                    quotes=[],  # synthetic — no source quotes
                    sources=["inferred"],
                    weekly_growth_pct=float(row.get("weekly_growth_pct") or 0),
                    open_window_days=int(row.get("open_window_days") or 14),
                    creator_brief=row.get("creator_brief", ""),
                    related_trends=list(row.get("related_trends") or [])[:3],
                )
            )
        except Exception:
            continue
    return out


def mine_demand_clusters_for_trends(
    keywords: list[str],
    posts: list[HNPost],
    *,
    max_clusters: int = 12,
    client: Optional[anthropic.Anthropic] = None,
    niche: str = DEFAULT_NICHE,
) -> list[DemandCluster]:
    """Legacy: run mine_demand_cluster across N trends, then dedupe + cap.

    Kept on the public surface for any caller that still wants the
    per-trend path. The orchestrator uses
    `mine_demand_clusters_from_comments` instead.
    """
    if client is None:
        client = anthropic.Anthropic()
    all_clusters: list[DemandCluster] = []
    for kw in keywords:
        comments = find_comments_for_keyword(kw, posts)
        all_clusters.extend(
            mine_demand_cluster(kw, comments, client=client, niche=niche)
        )
    deduped = dedupe_clusters(all_clusters)
    deduped.sort(key=lambda c: c.askers_estimate, reverse=True)
    return deduped[:max_clusters]
