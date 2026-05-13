"""Dated predictions + verdict tracking — the accountability layer.

Per BACKEND_BUILD §7 Step 10. Pipeline files a Prediction the first time a
trend crosses into Whisper or Builder, stores it in an append-only
data/predictions.jsonl, then re-checks it daily against the trend's
current lifecycle stage. Verdicts: verified / verified_early / wrong /
tracking / pending.

Lifecycle progression order is linear (whisper < builder < creator < hype
< commodity). 'verified' means today's lifecycle has reached or passed
the target_lifecycle by target_date; 'verified_early' means it reached
target_lifecycle before target_date; 'wrong' means target_date passed
without reaching target_lifecycle; 'tracking' means still on the way and
target_date hasn't arrived.

Surfaced spec extension: Prediction (in pipeline/models.py) gained three
optional fields — keyword, lifecycle_at_filing, target_lifecycle — so
verdict checking is deterministic instead of NLP-parsing the
prediction_text. Optional + None default = backwards-compatible with
the public data contract.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import anthropic
from pydantic import TypeAdapter

from pipeline.models import HitRate, LifecycleStage, Prediction
from pipeline.summarize import (
    HAIKU_MODEL,
    MAX_OUTPUT_TOKENS_CARD,
    _extract_json,
    _system_block,
)

PREDICTIONS_LOG_DEFAULT = Path("data") / "predictions.jsonl"

# Linear lifecycle progression — index reflects "maturity"
LIFECYCLE_INDEX: dict[LifecycleStage, int] = {
    "whisper": 0,
    "builder": 1,
    "creator": 2,
    "hype": 3,
    "commodity": 4,
}

PREDICTION_PROMPT_TEMPLATE = (
    "Trend keyword: {keyword}\n"
    "Current lifecycle stage: {current_lifecycle}\n"
    "Today: {today}\n\n"
    "Forecast when this trend will reach its next lifecycle stage. The lifecycle\n"
    "progression is: whisper -> builder -> creator -> hype -> commodity.\n\n"
    "Return JSON:\n"
    "- prediction_text: a single sentence stating the forecast with the target date\n"
    "- target_date: ISO date (YYYY-MM-DD), realistic given the current stage\n"
    '- target_lifecycle: one of "builder" | "creator" | "hype" | "commodity"'
)


def _reached_target(current: LifecycleStage, *, target: LifecycleStage) -> bool:
    return LIFECYCLE_INDEX[current] >= LIFECYCLE_INDEX[target]


# ---------- JSONL persistence ----------


def append_prediction(prediction: Prediction, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(prediction.model_dump_json() + "\n")


def load_predictions(path: Path) -> list[Prediction]:
    if not path.exists():
        return []
    out: list[Prediction] = []
    adapter = TypeAdapter(Prediction)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(adapter.validate_json(line))
    return out


def already_filed(
    existing: list[Prediction], *, keyword: str, target_lifecycle: LifecycleStage
) -> bool:
    """True if a prediction with this keyword+target_lifecycle exists in the log."""
    for p in existing:
        if p.keyword == keyword and p.target_lifecycle == target_lifecycle:
            return True
    return False


# ---------- Verdict update ----------


def update_verdict(
    prediction: Prediction,
    *,
    current_lifecycle: LifecycleStage,
    today: date,
) -> Prediction:
    """Refresh one prediction's verdict given today's lifecycle state.

    Verdicts that are already resolved (verified / verified_early / wrong)
    are returned unchanged — once decided, the call is final.
    """
    if prediction.verdict in ("verified", "verified_early", "wrong"):
        return prediction
    if prediction.target_lifecycle is None:
        # No deterministic criterion — leave as pending. NLP-parsing the
        # text isn't worth it.
        return prediction

    reached = _reached_target(current_lifecycle, target=prediction.target_lifecycle)
    if reached:
        if today < prediction.target_date:
            return prediction.model_copy(
                update={"verdict": "verified_early", "verified_at": today}
            )
        return prediction.model_copy(
            update={"verdict": "verified", "verified_at": today}
        )
    if today > prediction.target_date:
        return prediction.model_copy(update={"verdict": "wrong"})
    return prediction.model_copy(update={"verdict": "tracking"})


def update_all_verdicts(
    predictions: list[Prediction],
    *,
    current_lifecycles_by_keyword: dict[str, LifecycleStage],
    today: date,
) -> list[Prediction]:
    out: list[Prediction] = []
    for p in predictions:
        if p.keyword is None:
            out.append(p)
            continue
        current = current_lifecycles_by_keyword.get(p.keyword)
        if current is None:
            out.append(p)
            continue
        out.append(update_verdict(p, current_lifecycle=current, today=today))
    return out


# ---------- Hit rate ----------


def compute_hit_rate(predictions: list[Prediction]) -> HitRate:
    verified = sum(1 for p in predictions if p.verdict == "verified")
    verified_early = sum(1 for p in predictions if p.verdict == "verified_early")
    tracking = sum(1 for p in predictions if p.verdict == "tracking")
    wrong = sum(1 for p in predictions if p.verdict == "wrong")
    # 'tracking' and 'pending' are in-flight — only verified/verified_early/wrong
    # are final verdicts. Denominator counts only final outcomes.
    resolved = verified + verified_early + wrong
    rate = (verified + verified_early) / resolved if resolved else 0.0
    return HitRate(
        rate=rate,
        verified=verified,
        verified_early=verified_early,
        tracking=tracking,
        wrong=wrong,
    )


# ---------- Claude-driven generation ----------


def generate_prediction(
    *,
    keyword: str,
    current_lifecycle: LifecycleStage,
    today: date,
    user_niche: str = "AI tools for solo creators",
    client: Optional[anthropic.Anthropic] = None,
) -> Prediction:
    """One Haiku call producing a Prediction(filed_at=today, verdict='pending')."""
    if client is None:
        client = anthropic.Anthropic()

    prompt = PREDICTION_PROMPT_TEMPLATE.format(
        keyword=keyword, current_lifecycle=current_lifecycle, today=today.isoformat()
    )
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS_CARD,
        system=_system_block(user_niche),
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = _extract_json(response.content[0].text)
    target_date = date.fromisoformat(parsed["target_date"])
    return Prediction(
        keyword=keyword,
        text=parsed["prediction_text"],
        filed_at=today,
        target_date=target_date,
        verdict="pending",
        lifecycle_at_filing=current_lifecycle,
        target_lifecycle=parsed["target_lifecycle"],
    )
