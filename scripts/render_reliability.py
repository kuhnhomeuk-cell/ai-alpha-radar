"""Render public/reliability.png from data/predictions.jsonl.

Audit item 3.9. Reliability decomposition: x-axis = predicted probability
bucket midpoint, y-axis = actual hit rate within bucket. A perfectly
calibrated forecaster sits on y=x; deviations show over- or
under-confidence.

Run with: poetry run python scripts/render_reliability.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from pipeline import calibration  # noqa: E402
from pipeline.predict import load_predictions  # noqa: E402

OUTPUT_PATH = ROOT / "public" / "reliability.png"
DEFAULT_LOG = ROOT / "data" / "predictions.jsonl"


def main() -> int:
    preds = load_predictions(DEFAULT_LOG)
    bins = calibration.reliability_bins(preds, n_bins=4)
    brier = calibration.brier_score(preds)

    fig, ax = plt.subplots(figsize=(5, 5), dpi=120)
    ax.plot([0, 1], [0, 1], "--", color="#888", linewidth=1, label="perfect")
    if bins:
        xs = [b[0] for b in bins]
        ys = [b[1] for b in bins]
        sizes = [50 + 10 * b[2] for b in bins]
        ax.scatter(xs, ys, s=sizes, color="#2dd4bf", edgecolor="#0f766e", zorder=3)
        for x, y, n in bins:
            ax.annotate(f"n={n}", (x, y), textcoords="offset points", xytext=(8, 4),
                        color="#555", fontsize=9)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("predicted probability")
    ax.set_ylabel("actual hit rate")
    ax.set_title(f"prediction calibration · Brier = {brier:.3f}", fontsize=11)
    ax.grid(alpha=0.2)
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    fig.tight_layout()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PATH)
    plt.close(fig)
    print(f"wrote {OUTPUT_PATH}  (n_resolved={sum(b[2] for b in bins)}, brier={brier:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
