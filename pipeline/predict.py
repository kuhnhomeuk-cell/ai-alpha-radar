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
from typing import Any, Callable, Iterable, Optional

import anthropic
import numpy as np
from pydantic import TypeAdapter

from pipeline.models import HitRate, LifecycleStage, Prediction
from pipeline.summarize import (
    HAIKU_MODEL,
    MAX_OUTPUT_TOKENS_CARD,
    ClaudeParseError,
    _extract_json,
    _system_block,
)

PREDICTIONS_LOG_DEFAULT = Path("data") / "predictions.jsonl"

# Cosine distance ceiling for accepting an embedding-similarity match in
# build_lifecycle_lookup. More permissive than cluster_identity's 0.2: there
# we compare averaged cluster centroids (high internal coherence); here we
# compare individual keyword pairs that drift more under Claude paraphrasing.
LIFECYCLE_LOOKUP_THRESHOLD = 0.4

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


def _cosine_distance_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise cosine distance between rows of a (M×D) and rows of b (N×D)."""
    an = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    bn = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return 1.0 - an @ bn.T


def build_lifecycle_lookup(
    predictions: Iterable[Prediction],
    current_trends: list[Any],  # list[Trend] but avoiding circular import
    *,
    similarity_threshold: float = LIFECYCLE_LOOKUP_THRESHOLD,
    encode_fn: Optional[Callable[[list[str]], np.ndarray]] = None,
) -> dict[str, LifecycleStage]:
    """Map prediction.keyword → today's lifecycle stage.

    Bridges Claude's daily topic-name drift: the Haiku topic extraction
    paraphrases topics each run, so 'test-time compute scaling' yesterday
    may surface as 'inference-time compute scaling' today. Without a fuzzy
    fallback every stored prediction stays 'pending' forever and
    past_predictions in the snapshot is empty (the Star Log regression
    surfaced in the 2026-05-17 run).

    Strategy per prediction:
    1. Exact keyword match against today's trends (cheap, day-1 behavior).
    2. Embedding cosine-similarity match below similarity_threshold.
    3. No match → keyword stays out of the lookup, so update_all_verdicts
       leaves the prediction as 'pending' (legacy semantics for orphans).

    `encode_fn` is injectable so tests don't load MiniLM. Default uses the
    same sentence-transformers model the clustering step already runs on.
    """
    lookup: dict[str, LifecycleStage] = {
        t.keyword: t.lifecycle_stage for t in current_trends
    }
    candidates = [
        p.keyword for p in predictions
        if p.keyword and p.keyword not in lookup
    ]
    if not candidates or not current_trends:
        return lookup

    if encode_fn is None:
        from pipeline import cluster as cluster_mod

        def encode_fn(texts: list[str]) -> np.ndarray:  # type: ignore[misc]
            model = cluster_mod._get_model()
            return np.asarray(model.encode(texts, show_progress_bar=False))

    trend_keywords = [t.keyword for t in current_trends]
    trend_vecs = np.asarray(encode_fn(trend_keywords))
    pred_vecs = np.asarray(encode_fn(candidates))
    dists = _cosine_distance_matrix(pred_vecs, trend_vecs)
    best_idx = np.argmin(dists, axis=1)
    best_dist = dists[np.arange(len(candidates)), best_idx]
    for kw, idx, dist in zip(candidates, best_idx, best_dist):
        if dist < similarity_threshold:
            lookup[kw] = current_trends[int(idx)].lifecycle_stage
    return lookup


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
    raw_target = parsed.get("target_date")
    raw_text = parsed.get("prediction_text")
    raw_target_lc = parsed.get("target_lifecycle")
    if not (raw_target and raw_text and raw_target_lc):
        raise ClaudeParseError(
            f"prediction response missing required fields: "
            f"target_date={raw_target!r} text={raw_text!r} target_lifecycle={raw_target_lc!r}"
        )
    return Prediction(
        keyword=keyword,
        text=raw_text,
        filed_at=today,
        target_date=date.fromisoformat(raw_target),
        verdict="pending",
        lifecycle_at_filing=current_lifecycle,
        target_lifecycle=raw_target_lc,
    )
