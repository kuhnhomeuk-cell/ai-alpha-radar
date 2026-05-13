"""Day-1 backtest seeder for data/predictions.jsonl.

Picks the top N candidate terms from the cached Step 2-4 fixtures, files
a Prediction for each (target_lifecycle=builder, target_date=today+30,
verdict='pending'). The Star Log demo page reads this log so it's
populated even before the daily cron has accumulated snapshot history.

After this script runs once, the daily cron's predict.generate_prediction
takes over at flag time and the backtest script is redundant.

Run with: poetry run python scripts/backtest_predictions.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.fetch import arxiv, github, hackernews  # noqa: E402
from pipeline.models import Prediction  # noqa: E402
from pipeline.normalize import extract_candidate_terms  # noqa: E402
from pipeline.predict import PREDICTIONS_LOG_DEFAULT, append_prediction  # noqa: E402

BACKTEST_TOP_N = 15
BACKTEST_HORIZON_DAYS = 30


def main() -> int:
    fixtures = ROOT / "tests" / "fixtures"
    papers = arxiv.parse_atom_feed(
        (fixtures / "arxiv_sample.xml").read_text(encoding="utf-8"),
        categories=["cs.AI", "cs.LG", "cs.CL"],
    )
    posts = hackernews.parse_search_response(
        json.loads((fixtures / "hn_sample.json").read_text(encoding="utf-8"))
    )
    repos = github.parse_search_response(
        json.loads((fixtures / "github_sample.json").read_text(encoding="utf-8"))
    )

    terms = extract_candidate_terms(papers, posts, repos)
    # Rank by total cross-source presence as a velocity proxy
    ranked = sorted(
        terms,
        key=lambda t: -(t.arxiv_mentions + t.hn_mentions + t.github_mentions),
    )

    log = ROOT / PREDICTIONS_LOG_DEFAULT
    today = date.today()
    target = today + timedelta(days=BACKTEST_HORIZON_DAYS)

    written = 0
    for t in ranked[:BACKTEST_TOP_N]:
        # Skip single-character / suspect terms
        if len(t.canonical_form) < 2:
            continue
        pred = Prediction(
            keyword=t.canonical_form,
            text=(
                f"{t.canonical_form} reaches Builder stage by {target.isoformat()} "
                "based on current cross-source momentum."
            ),
            filed_at=today,
            target_date=target,
            verdict="pending",
            lifecycle_at_filing="whisper",
            target_lifecycle="builder",
        )
        append_prediction(pred, log)
        written += 1
        if written >= 10:
            # The §12 DoD wants ≥10; once we have that we still take everything
            # in the top N for richer demo data
            pass

    print(f"seeded {written} predictions into {log}")
    if written < 10:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
