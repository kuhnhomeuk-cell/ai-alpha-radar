"""ONE-CLUSTER VISUAL INSPECTION script for Step 11 — §5 checkpoint.

Fetches recent HN posts, filters for question-shaped niche-relevant
comments, runs HDBSCAN to find clusters, then fires ONE non-batch
Claude (Sonnet) call against the largest cluster. Pretty-prints the
resulting DemandCluster JSON for manual review.

This is the Karpathy §5 probe that MUST pass before any paid demand
batch fires in the orchestrator. Only run this on Dean's explicit go.

Cost: one Sonnet message — ~$0.01-0.02 for a ~600 input / ~600 output
token call. Cheap enough to repeat.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)
# Also try the parent (main) repo's .env.local — the operator keeps a
# single .env.local in ~/Desktop/AI Trends/ for both worktree + main.
load_dotenv(ROOT.parent.parent.parent / ".env.local", override=False)

import anthropic  # noqa: E402

from pipeline import demand  # noqa: E402
from pipeline.fetch import hackernews  # noqa: E402


def main() -> int:
    niche = "AI tools for solo creators"
    print("=== Step 1: fetch recent HN posts (live) ===")
    posts = hackernews.fetch_ai_posts(
        lookback_days=7,
        min_points=hackernews.HN_MIN_POINTS_KEYWORD,
        extra_passes=hackernews.EXTRA_PASS_NAMES,
    )
    hydrated = [p for p in posts if p.comments]
    print(
        f"  fetched {len(posts)} posts; {len(hydrated)} hydrated with comments"
    )
    if not hydrated:
        print("  NO hydrated posts — probe aborts.")
        return 2

    print("\n=== Step 2: gather question-shaped niche-relevant comments ===")
    gathered = demand.gather_question_comments(posts)
    print(f"  {len(gathered)} comments survived filters")
    if len(gathered) < demand.HDBSCAN_MIN_CLUSTER_SIZE_COMMENTS:
        print("  not enough comments to cluster — probe aborts.")
        return 3

    print("\n=== Step 3: HDBSCAN cluster ===")
    clusters = demand.cluster_comments_hdbscan(gathered)
    print(f"  {len(clusters)} non-noise clusters")
    clusters.sort(key=lambda c: len(c.comments), reverse=True)
    for c in clusters[:5]:
        print(f"  cluster_id={c.cluster_id}: {len(c.comments)} comments")
        for cmt, p in c.comments[:3]:
            preview = cmt.text[:80].replace("\n", " ")
            print(f"    - ({p.title[:30]}) {preview}")

    client = anthropic.Anthropic()

    if clusters:
        target = clusters[0]
        print(
            f"\n=== Step 4: probe cluster_id={target.cluster_id} "
            f"(size {len(target.comments)}) — Sonnet sync, ~$0.01-0.02 ==="
        )
        dc = demand.summarize_cluster_sync(target, client=client, niche=niche)
    else:
        # Sparse-data fallback path: run the legacy per-trend Sonnet pass on
        # ONE trend keyword (default "claude") as a sync probe against real
        # HN. This demonstrates the fallback path the orchestrator uses on
        # sparse days when HDBSCAN produces nothing.
        fallback_kw = "claude"
        print(
            f"\n=== Step 4 fallback: legacy per-trend probe on '{fallback_kw}' "
            f"(real HN, Sonnet sync, ~$0.01-0.03) ==="
        )
        matched = demand.find_comments_for_keyword(fallback_kw, posts)
        print(
            f"    {len(matched)} comments under posts mentioning '{fallback_kw}'"
        )
        legacy = demand.mine_demand_cluster(
            keyword=fallback_kw, comments=matched, client=client, niche=niche
        )
        dc = legacy[0] if legacy else None

    if dc is None:
        print("=== RESULT: Claude returned a malformed body or empty. ===")
        print("    Sync probe FAILED — DO NOT run the paid batch.")
        return 5

    print("=== RESULT (DemandCluster JSON) ===")
    print(dc.model_dump_json(indent=2))
    print("\n=== Inspect every field. ===")
    print("    Is this a real creator opportunity surface?")

    # End-to-end mini-run: now that the probe passed, run
    # mine_demand_clusters_from_comments with a small fallback set so we
    # can see the full path produce a list of clusters as the orchestrator
    # would. Cost: ~$0.06 (3 fallback Sonnet calls; sync probe already
    # billed above).
    print("\n=== Step 5: end-to-end fallback mini-run (3 trends) ===")
    fallback_kws = ["claude", "agents", "mcp"]
    out = demand.mine_demand_clusters_from_comments(
        posts,
        client=client,
        niche=niche,
        max_clusters=12,
        sync_probe=False,  # already probed above
        fallback_trend_keywords=fallback_kws,
    )
    print(f"  produced {len(out)} demand clusters")
    for i, c in enumerate(out, 1):
        print(f"\n  [{i}] {c.question_shape}")
        print(f"      askers={c.askers_estimate}, window={c.open_window_days}d")
        print(f"      brief: {c.creator_brief[:120]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
