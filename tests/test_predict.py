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


def test_load_predictions_raises_on_invalid_line(tmp_path: Path) -> None:
    """Audit item 1.8: malformed JSON lines surface as ValidationError rather
    than silent drops. Confirms the 15-vs-13 drift in production was NOT a
    parse error (which would have raised here too)."""
    log = tmp_path / "predictions.jsonl"
    log.write_text("{not a json line}\n", encoding="utf-8")
    with pytest.raises(Exception):
        predict.load_predictions(log)


def test_update_all_verdicts_preserves_count(tmp_path: Path) -> None:
    """Audit item 1.8: update_all_verdicts MUST NOT lose or add predictions.
    Predictions whose keyword is no longer in today's top-N stay as-is
    (verdict='pending'); they are not dropped."""
    preds = [
        _pred("alive", filed=date(2026, 5, 1), target=date(2026, 6, 1)),
        _pred("orphaned", filed=date(2026, 5, 1), target=date(2026, 6, 1)),
    ]
    updated = predict.update_all_verdicts(
        preds, current_lifecycles_by_keyword={"alive": "builder"}, today=date(2026, 5, 13)
    )
    assert len(updated) == len(preds)
    by_kw = {p.keyword: p for p in updated}
    assert by_kw["alive"].verdict == "verified_early"
    assert by_kw["orphaned"].verdict == "pending"


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


# ---------- build_lifecycle_lookup (embedding-fallback for verdicts) ----------


class _FakeTrend:
    """Minimal Trend stand-in for build_lifecycle_lookup.

    The real Trend model has 30+ fields; only keyword and lifecycle_stage are
    consulted here, so the tests assert behavior without instantiating the
    full Pydantic schema (which would also pull in the embedding model).
    """

    def __init__(self, keyword: str, lifecycle_stage: str) -> None:
        self.keyword = keyword
        self.lifecycle_stage = lifecycle_stage


def _stub_encoder(vectors_by_text: dict[str, list[float]]):
    """Return an encoder fn that maps texts → preset vectors. Raises on unknown
    input so a test that produces an unexpected lookup fails loudly."""
    import numpy as np

    def _encode(texts: list[str]) -> np.ndarray:
        return np.asarray([vectors_by_text[t] for t in texts], dtype=float)

    return _encode


def test_build_lifecycle_lookup_exact_match_populates_directly() -> None:
    preds = [_pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1))]
    trends = [_FakeTrend("alpha", "builder"), _FakeTrend("beta", "creator")]
    # encode_fn must NOT be called when every prediction has an exact match.
    def boom(_texts):  # pragma: no cover - if invoked, test fails
        raise AssertionError("encoder should not run for exact matches")

    lookup = predict.build_lifecycle_lookup(preds, trends, encode_fn=boom)
    assert lookup["alpha"] == "builder"
    assert lookup["beta"] == "creator"


def test_build_lifecycle_lookup_fuzzy_match_below_threshold() -> None:
    """Structural fix: Claude paraphrases topics each day, so the prediction
    keyword 'test-time compute scaling' may never literally match today's
    trend 'inference-time compute scaling'. The embedding-similarity fallback
    must bridge that drift."""
    preds = [_pred("test-time compute scaling", filed=date(2026, 5, 1), target=date(2026, 6, 1))]
    trends = [
        _FakeTrend("inference-time compute scaling", "builder"),
        _FakeTrend("crystal structure generation", "whisper"),
    ]
    # Near-aligned vectors for the paraphrase, orthogonal for the unrelated one.
    encoder = _stub_encoder({
        "test-time compute scaling":      [1.0, 0.0, 0.0],
        "inference-time compute scaling": [0.95, 0.05, 0.0],
        "crystal structure generation":   [0.0, 0.0, 1.0],
    })
    lookup = predict.build_lifecycle_lookup(
        preds, trends, encode_fn=encoder, similarity_threshold=0.4
    )
    assert lookup["test-time compute scaling"] == "builder"


def test_build_lifecycle_lookup_omits_when_nothing_close() -> None:
    """Unrelated keyword above threshold MUST stay out of the lookup so
    update_all_verdicts leaves it as 'pending' (legacy semantics)."""
    preds = [_pred("alpha", filed=date(2026, 5, 1), target=date(2026, 6, 1))]
    trends = [_FakeTrend("zeta", "builder")]
    encoder = _stub_encoder({
        "alpha": [1.0, 0.0, 0.0],
        "zeta":  [0.0, 1.0, 0.0],  # orthogonal → distance ≈ 1.0
    })
    lookup = predict.build_lifecycle_lookup(
        preds, trends, encode_fn=encoder, similarity_threshold=0.4
    )
    assert "alpha" not in lookup
    assert lookup["zeta"] == "builder"


def test_build_lifecycle_lookup_skips_predictions_with_no_keyword() -> None:
    """Legacy predictions filed before the keyword field existed have
    keyword=None; they must not trigger encoding."""
    p = Prediction(
        text="x", filed_at=date(2026, 5, 1), target_date=date(2026, 6, 1),
        verdict="pending", keyword=None, target_lifecycle=None,
    )
    trends = [_FakeTrend("alpha", "builder")]
    def boom(_texts):  # pragma: no cover - keyword=None must short-circuit
        raise AssertionError("encoder must not run for keyword=None preds")
    lookup = predict.build_lifecycle_lookup([p], trends, encode_fn=boom)
    assert lookup == {"alpha": "builder"}


def test_update_all_verdicts_uses_lookup_from_build_lifecycle_lookup() -> None:
    """End-to-end: pipeline composes build_lifecycle_lookup → update_all_verdicts.
    The fuzzy-matched prediction must reach 'verified_early' instead of staying
    'pending', which is the whole point of the fix."""
    preds = [
        _pred(
            "test-time compute scaling",
            filed=date(2026, 5, 1),
            target=date(2026, 6, 1),
            target_lc="builder",
            filing_lc="whisper",
        ),
    ]
    trends = [_FakeTrend("inference-time compute scaling", "builder")]
    encoder = _stub_encoder({
        "test-time compute scaling":      [1.0, 0.0, 0.0],
        "inference-time compute scaling": [0.95, 0.05, 0.0],
    })
    lookup = predict.build_lifecycle_lookup(preds, trends, encode_fn=encoder)
    updated = predict.update_all_verdicts(preds, current_lifecycles_by_keyword=lookup, today=date(2026, 5, 20))
    assert updated[0].verdict == "verified_early"


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
