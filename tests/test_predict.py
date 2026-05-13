"""TDD for pipeline.predict — JSONL persistence + verdict updater.

The Claude-call paths are tested against the same FakeAnthropic stub
pattern as test_summarize.py.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pipeline import predict
from pipeline.models import Prediction


# ---------- fixtures ----------


def _pred(
    keyword: str,
    *,
    filed: date,
    target: date,
    target_lc: str = "builder",
    filing_lc: str = "whisper",
    verdict: str = "pending",
    text: str = "X will reach Builder by target date.",
) -> Prediction:
    return Prediction(
        text=text,
        filed_at=filed,
        target_date=target,
        verdict=verdict,
        keyword=keyword,
        lifecycle_at_filing=filing_lc,
        target_lifecycle=target_lc,
    )


# ---------- ordering ----------


def test_lifecycle_ordering_index() -> None:
    assert predict.LIFECYCLE_INDEX["whisper"] == 0
    assert predict.LIFECYCLE_INDEX["builder"] == 1
    assert predict.LIFECYCLE_INDEX["creator"] == 2
    assert predict.LIFECYCLE_INDEX["hype"] == 3
    assert predict.LIFECYCLE_INDEX["commodity"] == 4


def test_lifecycle_advanced_past_returns_true_when_target_reached() -> None:
    assert predict._reached_target("builder", target="builder") is True
    assert predict._reached_target("creator", target="builder") is True


def test_lifecycle_advanced_past_returns_false_when_not_yet() -> None:
    assert predict._reached_target("whisper", target="builder") is False


# ---------- JSONL ----------


def test_append_then_load_predictions_roundtrip(tmp_path: Path) -> None:
    log_path = tmp_path / "predictions.jsonl"
    p1 = _pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1))
    p2 = _pred("beta", filed=date(2026, 5, 2), target=date(2026, 6, 5), target_lc="creator")
    predict.append_prediction(p1, log_path)
    predict.append_prediction(p2, log_path)

    loaded = predict.load_predictions(log_path)
    assert len(loaded) == 2
    assert loaded[0].keyword == "alpha"
    assert loaded[1].target_lifecycle == "creator"


def test_load_predictions_missing_file_returns_empty(tmp_path: Path) -> None:
    assert predict.load_predictions(tmp_path / "nope.jsonl") == []


# ---------- verdict updater ----------


def test_verdict_verified_when_target_lifecycle_reached_by_target_date() -> None:
    p = _pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1))
    out = predict.update_verdict(
        p, current_lifecycle="builder", today=date(2026, 6, 1)
    )
    assert out.verdict == "verified"
    assert out.verified_at == date(2026, 6, 1)


def test_verdict_verified_early_when_target_reached_before_target_date() -> None:
    p = _pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 15))
    out = predict.update_verdict(
        p, current_lifecycle="creator", today=date(2026, 5, 25)
    )
    assert out.verdict == "verified_early"
    assert out.verified_at == date(2026, 5, 25)


def test_verdict_wrong_when_target_not_reached_after_target_date() -> None:
    p = _pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1))
    out = predict.update_verdict(
        p, current_lifecycle="whisper", today=date(2026, 6, 5)
    )
    assert out.verdict == "wrong"


def test_verdict_tracking_when_not_yet_target_date_and_not_reached() -> None:
    p = _pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 30))
    out = predict.update_verdict(
        p, current_lifecycle="whisper", today=date(2026, 5, 20)
    )
    assert out.verdict == "tracking"


def test_verdict_idempotent_on_already_verified() -> None:
    # A prediction already marked 'verified' should not flip back to 'tracking'
    p = _pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="verified")
    out = predict.update_verdict(
        p, current_lifecycle="whisper", today=date(2026, 6, 5)
    )
    assert out.verdict == "verified"


# ---------- HitRate ----------


def test_compute_hit_rate_counts() -> None:
    preds = [
        _pred("a", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="verified"),
        _pred("b", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="verified_early"),
        _pred("c", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="verified"),
        _pred("d", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="tracking"),
        _pred("e", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="wrong"),
        _pred("f", filed=date(2026, 5, 1), target=date(2026, 6, 1), verdict="pending"),
    ]
    hit = predict.compute_hit_rate(preds)
    assert hit.verified == 2
    assert hit.verified_early == 1
    assert hit.tracking == 1
    assert hit.wrong == 1
    # rate = (verified + verified_early) / resolved (= 4); pending excluded
    assert hit.rate == pytest.approx(3 / 4)


def test_compute_hit_rate_empty_returns_zero_rate() -> None:
    hit = predict.compute_hit_rate([])
    assert hit.rate == 0.0
    assert hit.verified == hit.verified_early == hit.tracking == hit.wrong == 0


# ---------- already_filed ----------


def test_already_filed_returns_true_when_same_keyword_target_lifecycle_exists() -> None:
    existing = [_pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1))]
    assert predict.already_filed(existing, keyword="alpha", target_lifecycle="builder")
    assert not predict.already_filed(existing, keyword="alpha", target_lifecycle="creator")
    assert not predict.already_filed(existing, keyword="beta", target_lifecycle="builder")


# ---------- generate_prediction (Claude-mocked) ----------


class FakeAnthropic:
    def __init__(self, response_text: str) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        self._response_text = response_text

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text=self._response_text)])


def test_generate_prediction_parses_claude_output() -> None:
    fake = FakeAnthropic(
        json.dumps(
            {
                "prediction_text": "World-model agents reach Builder by 2026-06-15.",
                "target_date": "2026-06-15",
                "target_lifecycle": "builder",
            }
        )
    )
    p = predict.generate_prediction(
        keyword="world-model-agents",
        current_lifecycle="whisper",
        today=date(2026, 5, 13),
        client=fake,
    )
    assert p.text.startswith("World-model agents")
    assert p.target_date == date(2026, 6, 15)
    assert p.target_lifecycle == "builder"
    assert p.lifecycle_at_filing == "whisper"
    assert p.filed_at == date(2026, 5, 13)
    assert p.verdict == "pending"
    assert p.keyword == "world-model-agents"
