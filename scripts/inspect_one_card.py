"""ONE-CARD VISUAL INSPECTION script for Step 9 — §5 checkpoint.

Runs the full enrich_card path against the live Anthropic API on a single
representative CardInput. Pretty-prints the resulting CardOutput JSON for
manual review. Only run this on Dean's explicit go.

Cost: ~$0.01 (4 Haiku 4.5 calls, ~600 input + 300 output tokens each).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local", override=True)

from pipeline.summarize import CardInput, enrich_card  # noqa: E402


def main() -> int:
    card = CardInput(
        keyword="world-model-agents",
        cluster_label="Autonomous Reasoning",
        related_terms=["world models", "browser agents", "tool calling"],
        arxiv_papers_7d=8,
        github_repos_7d=5,
        hn_posts_7d=12,
        velocity_score=3.4,
        saturation=22.0,
        convergence_detected=True,
        lifecycle_stage="builder",
        user_niche="AI tools for solo creators",
    )

    print("=== INPUT ===")
    print(card.model_dump_json(indent=2))
    print("\n=== Calling Anthropic API (4 sequential Haiku 4.5 prompts)... ===\n")

    output = enrich_card(card)

    print("=== OUTPUT ===")
    print(output.model_dump_json(indent=2))
    print("\n=== Done. Inspect every field above. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
