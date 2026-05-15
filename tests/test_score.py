"""Hand-computed TDD for pipeline.score — every assertion comes with the math.

Every test here matches the explicit formula in BACKEND_BUILD §7 Step 7 /
PLAN.md §6 — no "good enough" tolerances, no leaving the math to inference.
"""

from datetime import datetime, timezone

import pytest

from pipeline import score
from pipeline.models import ConvergenceEvent


# -------- velocity --------------------------------------------------------


def test_velocity_normal_case() -> None:
    # mentions_7d=20, mentions_30d=30
    # floored_30d = max(30, 10) = 30
    # expected_7d = 30/30 * 7 = 7.0
    # denom = max(7.0, 1) = 7.0
    # velocity = 20 / 7.0 = 2.857142857
    assert score.velocity(20, 30) == pytest.approx(20 / 7)


def test_velocity_floor_on_30d_count() -> None:
    # mentions_7d=10, mentions_30d=5 — floored to 10
    # expected_7d = 10/30 * 7 = 2.333...
    # velocity = 10 / 2.333... = 4.2857
    assert score.velocity(10, 5) == pytest.approx(10 / (10 / 30 * 7))


def test_velocity_zero_7d_returns_zero() -> None:
    assert score.velocity(0, 20) == 0.0


def test_velocity_denominator_floor_at_one() -> None:
    # mentions_7d=5, mentions_30d=0 → floored=10 → expected=2.333
    assert score.velocity(5, 0) == pytest.approx(5 / (10 / 30 * 7))


# -------- saturation ------------------------------------------------------


def test_saturation_uniform_50() -> None:
    # 0.35*50 + 0.30*50 + 0.20*50 + 0.15*50 = 50.0
    assert score.saturation(github=50, hn=50, arxiv=50, semantic_scholar=50) == 50.0


def test_saturation_only_github_at_100() -> None:
    # 0.35*100 = 35
    assert score.saturation(github=100, hn=0, arxiv=0, semantic_scholar=0) == pytest.approx(35.0)


def test_saturation_only_hn_at_100() -> None:
    # 0.30*100 = 30
    assert score.saturation(github=0, hn=100, arxiv=0, semantic_scholar=0) == pytest.approx(30.0)


def test_saturation_bounds_zero() -> None:
    assert score.saturation(github=0, hn=0, arxiv=0, semantic_scholar=0) == 0.0


def test_saturation_bounds_hundred() -> None:
    # 35 + 30 + 20 + 15 = 100
    assert score.saturation(
        github=100, hn=100, arxiv=100, semantic_scholar=100
    ) == pytest.approx(100.0)


# -------- hidden_gem ------------------------------------------------------


def test_hidden_gem_normal_case() -> None:
    # velocity=5 → vnorm=0.5
    # 1 - 20/100 = 0.8
    # 0.4*0.5 + 0.35*0.8 + 0.25*0.6 = 0.2 + 0.28 + 0.15 = 0.63
    assert score.hidden_gem(5, 20, 0.6) == pytest.approx(0.63)


def test_hidden_gem_velocity_clipped_at_ten() -> None:
    # velocity=20 → clipped to 10 → vnorm=1.0
    # 0.4*1.0 + 0.35*(1-50/100) + 0.25*0.5 = 0.4 + 0.175 + 0.125 = 0.7
    assert score.hidden_gem(20, 50, 0.5) == pytest.approx(0.7)


def test_hidden_gem_floor_at_zero() -> None:
    # velocity=0, sat=100, builder=0 → all terms 0
    assert score.hidden_gem(0, 100, 0) == 0.0


def test_hidden_gem_ceiling_at_one() -> None:
    # velocity=100 (clipped 10), sat=0, builder=1.0
    # 0.4*1.0 + 0.35*1.0 + 0.25*1.0 = 1.0
    assert score.hidden_gem(100, 0, 1) == pytest.approx(1.0)


# -------- lifecycle_stage -------------------------------------------------


def test_lifecycle_whisper_when_arxiv_only_low_repos_low_sat_velocity_above_one_five() -> None:
    stage = score.lifecycle_stage(
        arxiv_30d=5,
        github_repos_7d=2,
        hn_points_7d=20,
        saturation=10,
        velocity=2.0,
        builder_signal=0.3,
    )
    assert stage == "whisper"


def test_lifecycle_builder_when_repos_at_three_sat_under_thirtyfive_signal_strong() -> None:
    stage = score.lifecycle_stage(
        arxiv_30d=10,
        github_repos_7d=5,
        hn_points_7d=50,
        saturation=20,
        velocity=1.5,
        builder_signal=0.8,
    )
    assert stage == "builder"


def test_lifecycle_creator_when_saturation_in_mid_band() -> None:
    stage = score.lifecycle_stage(
        arxiv_30d=20,
        github_repos_7d=20,
        hn_points_7d=200,
        saturation=45,
        velocity=1.5,
        builder_signal=0.7,
    )
    assert stage == "creator"


def test_lifecycle_hype_when_sat_above_sixty_velocity_above_two() -> None:
    stage = score.lifecycle_stage(
        arxiv_30d=30,
        github_repos_7d=50,
        hn_points_7d=200,
        saturation=70,
        velocity=2.5,
        builder_signal=0.9,
    )
    assert stage == "hype"


def test_lifecycle_commodity_when_high_sat_low_velocity_many_repos() -> None:
    stage = score.lifecycle_stage(
        arxiv_30d=50,
        github_repos_7d=150,
        hn_points_7d=50,
        saturation=80,
        velocity=1.0,
        builder_signal=0.5,
    )
    assert stage == "commodity"


def test_lifecycle_default_whisper_when_no_rule_matches() -> None:
    # arxiv_30d=0 disqualifies Whisper; everything else also fails its rule
    stage = score.lifecycle_stage(
        arxiv_30d=0,
        github_repos_7d=1,
        hn_points_7d=0,
        saturation=15,
        velocity=0.5,
        builder_signal=0.1,
    )
    assert stage == "whisper"


# -------- tbts ------------------------------------------------------------


def test_tbts_builder_with_convergence_returns_64() -> None:
    # vnorm = min(5,10)/10 = 0.5
    # hg = 0.7
    # lifecycle_weight (builder) = 0.50
    # conv = 1.0
    # raw = 0.35*0.5 + 0.30*0.7 + 0.20*0.50 + 0.15*1.0 = 0.175 + 0.21 + 0.10 + 0.15 = 0.635
    # tbts = round(63.5) = 64
    score_int = score.tbts(
        velocity_score=5,
        hidden_gem_score=0.7,
        lifecycle="builder",
        convergence_detected=True,
    )
    assert score_int == 64


def test_tbts_whisper_no_convergence() -> None:
    # vnorm=0.3, hg=0.5, lc_w=0.20, conv=0
    # raw = 0.35*0.3 + 0.30*0.5 + 0.20*0.20 + 0.15*0 = 0.105 + 0.15 + 0.04 + 0 = 0.295
    # tbts = 30 (Python's banker's rounding: round(29.5)=30)
    val = score.tbts(
        velocity_score=3,
        hidden_gem_score=0.5,
        lifecycle="whisper",
        convergence_detected=False,
    )
    assert val == 30


def test_tbts_clipped_velocity() -> None:
    # vnorm clamps at 1.0 when velocity > 10
    val = score.tbts(
        velocity_score=100,
        hidden_gem_score=1.0,
        lifecycle="hype",
        convergence_detected=True,
    )
    # raw = 0.35*1 + 0.30*1 + 0.20*0.40 + 0.15*1 = 0.35 + 0.30 + 0.08 + 0.15 = 0.88
    assert val == 88


# -------- detect_convergence ----------------------------------------------


def test_detect_convergence_three_sources_in_72h_window() -> None:
    appearances = {
        "arxiv": datetime(2026, 5, 10, 4, 0, tzinfo=timezone.utc),
        "hackernews": datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
        "github": datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc),
    }
    event = score.detect_convergence(appearances)
    assert isinstance(event, ConvergenceEvent)
    assert event.detected is True
    assert set(event.sources_hit) == {"arxiv", "hackernews", "github"}
    assert event.window_hours == 72


def test_detect_convergence_only_two_sources_returns_false() -> None:
    appearances = {
        "arxiv": datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        "hackernews": datetime(2026, 5, 11, 0, 0, tzinfo=timezone.utc),
    }
    event = score.detect_convergence(appearances)
    assert event.detected is False


def test_detect_convergence_three_sources_spread_too_wide() -> None:
    # gap > 72h between adjacent → no valid window contains all 3
    appearances = {
        "arxiv": datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),
        "hackernews": datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),  # +96h
        "github": datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc),  # +168h
    }
    event = score.detect_convergence(appearances)
    assert event.detected is False


def test_detect_convergence_four_sources_picks_tightest_window() -> None:
    appearances = {
        "arxiv": datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc),  # outlier
        "hackernews": datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        "github": datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
        "semantic_scholar": datetime(2026, 5, 12, 18, 0, tzinfo=timezone.utc),
    }
    event = score.detect_convergence(appearances)
    assert event.detected is True
    # arxiv should NOT be in sources_hit (it's >72h before the cluster)
    assert "arxiv" not in event.sources_hit
    assert set(event.sources_hit) == {"hackernews", "github", "semantic_scholar"}


# -------- mann_kendall_confidence ----------------------------------------


def test_mann_kendall_strong_upward_trend() -> None:
    series = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    z = score.mann_kendall_confidence(series)
    assert z > 1.96, f"expected 95% confident upward; got z={z}"


def test_mann_kendall_strong_downward_trend_returns_negative_z() -> None:
    series = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    z = score.mann_kendall_confidence(series)
    assert z < -1.96, f"expected 95% confident downward; got z={z}"


def test_mann_kendall_flat_series_near_zero() -> None:
    series = [5, 5, 5, 5, 5, 5, 5, 5]
    z = score.mann_kendall_confidence(series)
    assert abs(z) < 1.0


def test_mann_kendall_short_series_returns_zero() -> None:
    # Mann-Kendall needs at least ~4 points to be meaningful
    assert score.mann_kendall_confidence([1, 2]) == 0.0
    assert score.mann_kendall_confidence([]) == 0.0


# -------- velocity_from_topic_docs (v0.1.1) -------------------------------


def test_velocity_from_topic_docs_counts_7d_and_30d_windows() -> None:
    today = datetime(2026, 5, 14, tzinfo=timezone.utc)
    source_doc_ids = {"arxiv": ["a/1", "a/2", "a/3"]}
    doc_timestamps = {
        ("arxiv", "a/1"): datetime(2026, 5, 13, tzinfo=timezone.utc),  # 1d ago
        ("arxiv", "a/2"): datetime(2026, 5, 10, tzinfo=timezone.utc),  # 4d ago
        ("arxiv", "a/3"): datetime(2026, 4, 25, tzinfo=timezone.utc),  # 19d ago
    }
    m7, m30, v = score.velocity_from_topic_docs(
        source_doc_ids=source_doc_ids,
        doc_timestamps=doc_timestamps,
        today=today,
    )
    assert m7 == 2
    assert m30 == 3
    # floored_30d=max(3,10)=10; expected=10/30*7≈2.333; v=2/2.333≈0.857
    assert v == pytest.approx(2 / (10 / 30 * 7))


def test_velocity_from_topic_docs_aggregates_across_sources() -> None:
    today = datetime(2026, 5, 14, tzinfo=timezone.utc)
    one_day_ago = datetime(2026, 5, 13, tzinfo=timezone.utc)
    m7, m30, _ = score.velocity_from_topic_docs(
        source_doc_ids={
            "arxiv": ["a/1"],
            "hackernews": [42],
            "github": ["acme/foo"],
        },
        doc_timestamps={
            ("arxiv", "a/1"): one_day_ago,
            ("hackernews", 42): one_day_ago,
            ("github", "acme/foo"): one_day_ago,
        },
        today=today,
    )
    assert m7 == 3
    assert m30 == 3


def test_velocity_from_topic_docs_missing_timestamp_ignored() -> None:
    today = datetime(2026, 5, 14, tzinfo=timezone.utc)
    m7, m30, v = score.velocity_from_topic_docs(
        source_doc_ids={"arxiv": ["unknown"]},
        doc_timestamps={},
        today=today,
    )
    assert m7 == 0
    assert m30 == 0
    assert v == 0.0


def test_velocity_from_topic_docs_empty_source_doc_ids() -> None:
    today = datetime(2026, 5, 14, tzinfo=timezone.utc)
    m7, m30, v = score.velocity_from_topic_docs(
        source_doc_ids={}, doc_timestamps={}, today=today,
    )
    assert (m7, m30, v) == (0, 0, 0.0)


def test_velocity_from_topic_docs_doc_outside_30d_window_excluded() -> None:
    today = datetime(2026, 5, 14, tzinfo=timezone.utc)
    old = datetime(2026, 3, 1, tzinfo=timezone.utc)  # >60d ago
    m7, m30, _ = score.velocity_from_topic_docs(
        source_doc_ids={"arxiv": ["a/old"]},
        doc_timestamps={("arxiv", "a/old"): old},
        today=today,
    )
    assert m7 == 0
    assert m30 == 0


# ---------- v0.2.0 — venue_boost from arxiv:comment ----------


def test_venue_boost_fires_on_top_venue_with_recent_year() -> None:
    assert score.venue_boost("ICML2026") == 0.5
    assert score.venue_boost("Accepted at NeurIPS 2026") == 0.5
    assert score.venue_boost("ICLR 2027 oral") == 0.5
    assert score.venue_boost("CVPR 2026") == 0.5


def test_venue_boost_zero_on_non_venue_or_old_year() -> None:
    assert score.venue_boost("") == 0.0
    assert score.venue_boost("Work in Progress") == 0.0
    assert score.venue_boost("21 pages, 5 figures") == 0.0
    # Pre-2025 venues don't qualify (pattern is 2025-2039).
    assert score.venue_boost("ICML 2020") == 0.0


# ---------- v0.2.0 — cross-source consensus ----------


def test_cross_source_consensus_full_match() -> None:
    active = ["arxiv", "github", "hackernews", "reddit", "huggingface"]
    assert score.cross_source_consensus(active, len(active)) == 1.0


def test_cross_source_consensus_partial_match() -> None:
    confirming = ["arxiv", "github", "hackernews"]
    assert score.cross_source_consensus(confirming, 5) == pytest.approx(0.6)


def test_cross_source_consensus_zero_active_returns_zero() -> None:
    """Defensive: never divide by zero even if every fetcher failed."""
    assert score.cross_source_consensus([], 0) == 0.0
    assert score.cross_source_consensus(["arxiv"], 0) == 0.0


def test_cross_source_consensus_caps_at_one() -> None:
    """If somehow confirming exceeds active, cap at 1.0."""
    assert score.cross_source_consensus(["a", "b", "c"], 2) == 1.0
