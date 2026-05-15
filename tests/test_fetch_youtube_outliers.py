"""TDD for pipeline.fetch.youtube_outliers (Wave 5 — VidIQ-backed outliers)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline.fetch import youtube_outliers as yo
from pipeline.models import YoutubeOutlier

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "vidiq_outliers_sample.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ---------- parse_outliers_response ----------


def test_parse_outliers_response_extracts_videos() -> None:
    outliers = yo.parse_outliers_response(_load_fixture())
    assert len(outliers) == 5
    assert all(isinstance(o, YoutubeOutlier) for o in outliers)


def test_parse_outliers_response_maps_fields_correctly() -> None:
    outliers = yo.parse_outliers_response(_load_fixture())
    by_id = {o.video_id: o for o in outliers}
    # First fixture entry — extreme breakout
    first = by_id["4gS2wYVep-8"]
    assert first.title == "The Truth About Digital Marketing Courses & AI Tools"
    assert first.channel_name == "OfficialAngel"  # whitespace stripped
    assert first.view_count == 11609
    assert first.outlier_multiple == pytest.approx(1335.84)
    # baseline = view_count / breakoutScore
    assert first.channel_baseline_views == int(11609 / 1335.84)
    assert first.thumbnail_url.startswith("https://i.ytimg.com/")
    assert first.published_at == datetime.fromtimestamp(1777957568, tz=timezone.utc)


def test_parse_outliers_response_combines_topics_and_tags() -> None:
    outliers = yo.parse_outliers_response(_load_fixture())
    by_id = {o.video_id: o for o in outliers}
    # SaaS Inspection entry has both topics and tags
    saas = by_id["l9maxbsOuuQ"]
    assert "Knowledge" in saas.key_topics
    assert "Technology" in saas.key_topics
    assert "ai tools for tutorials" in saas.key_topics


def test_parse_outliers_response_empty_videos_returns_empty() -> None:
    assert yo.parse_outliers_response({"videos": []}) == []
    assert yo.parse_outliers_response({}) == []


def test_parse_outliers_response_skips_malformed_entries() -> None:
    """A single malformed entry must not crash the parser."""
    payload = {
        "videos": [
            {
                "videoId": "ok-1",
                "videoTitle": "OK 1",
                "channelTitle": "C1",
                "viewCount": 100,
                "breakoutScore": 5.0,
                "videoPublishedAt": 1700000000,
                "videoThumbnail": "https://x/t.jpg",
                "videoTopics": [],
                "videoTags": [],
            },
            {"videoId": "missing-everything-else"},  # malformed — skip
            {
                "videoId": "ok-2",
                "videoTitle": "OK 2",
                "channelTitle": "C2",
                "viewCount": 200,
                "breakoutScore": 8.0,
                "videoPublishedAt": 1700000001,
                "videoThumbnail": "https://x/t2.jpg",
                "videoTopics": [],
                "videoTags": [],
            },
        ]
    }
    outliers = yo.parse_outliers_response(payload)
    assert [o.video_id for o in outliers] == ["ok-1", "ok-2"]


def test_parse_outliers_response_handles_zero_breakout() -> None:
    """A breakoutScore of 0 must not divide-by-zero; baseline defaults to 0."""
    payload = {
        "videos": [
            {
                "videoId": "zero",
                "videoTitle": "Zero breakout",
                "channelTitle": "Ch",
                "viewCount": 500,
                "breakoutScore": 0.0,
                "videoPublishedAt": 1700000000,
                "videoThumbnail": "https://x/t.jpg",
                "videoTopics": [],
                "videoTags": [],
            }
        ]
    }
    outliers = yo.parse_outliers_response(payload)
    assert len(outliers) == 1
    assert outliers[0].channel_baseline_views == 0
    assert outliers[0].outlier_multiple == 0.0


# ---------- dedupe + top_n ----------


def _o(video_id: str, mult: float, view_count: int = 1000) -> YoutubeOutlier:
    return YoutubeOutlier(
        video_id=video_id,
        title=video_id,
        channel_name="x",
        view_count=view_count,
        channel_baseline_views=int(view_count / max(mult, 0.01)),
        outlier_multiple=mult,
        published_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        thumbnail_url="https://x/t.jpg",
    )


def test_dedupe_outliers_keeps_highest_outlier_multiple() -> None:
    items = [_o("a", 3.0), _o("b", 5.0), _o("a", 7.0), _o("c", 2.0), _o("a", 4.0)]
    deduped = yo.dedupe_outliers(items)
    by_id = {o.video_id: o for o in deduped}
    assert len(deduped) == 3
    assert by_id["a"].outlier_multiple == 7.0
    assert by_id["b"].outlier_multiple == 5.0
    assert by_id["c"].outlier_multiple == 2.0


def test_top_n_sorts_and_caps() -> None:
    items = [_o("a", 3.0), _o("b", 9.0), _o("c", 1.0), _o("d", 5.0)]
    ranked = yo.top_n(items, n=2)
    assert [o.video_id for o in ranked] == ["b", "d"]


# ---------- disk reader ----------


def test_load_outliers_from_disk_missing_file_returns_empty(tmp_path: Path) -> None:
    assert yo.load_outliers_from_disk(tmp_path / "nope.json") == []


def test_load_outliers_from_disk_malformed_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json at all")
    assert yo.load_outliers_from_disk(p) == []


def test_load_outliers_from_disk_valid_file(tmp_path: Path) -> None:
    p = tmp_path / "good.json"
    items = [_o("a", 5.0), _o("b", 3.0)]
    payload = {
        "refreshed_at": "2026-05-15T00:00:00+00:00",
        "keywords_queried": ["AI tools for creators"],
        "outliers": [o.model_dump(mode="json") for o in items],
    }
    p.write_text(json.dumps(payload))
    loaded = yo.load_outliers_from_disk(p)
    assert len(loaded) == 2
    assert {o.video_id for o in loaded} == {"a", "b"}


def test_load_outliers_from_disk_skips_invalid_entries(tmp_path: Path) -> None:
    p = tmp_path / "mixed.json"
    p.write_text(
        json.dumps(
            {
                "outliers": [
                    {"video_id": "bad"},  # missing many required fields
                    _o("good", 5.0).model_dump(mode="json"),
                ]
            }
        )
    )
    loaded = yo.load_outliers_from_disk(p)
    assert [o.video_id for o in loaded] == ["good"]


# ---------- build_outliers_cache ----------


def test_build_outliers_cache_dedupes_and_ranks() -> None:
    per_kw = {
        "AI tools for creators": [_o("a", 3.0), _o("b", 5.0)],
        "Claude AI": [_o("b", 8.0), _o("c", 2.0)],  # 'b' shows up with higher multiple
    }
    cache = yo.build_outliers_cache(per_kw, top_n_cap=10)
    assert cache["keywords_queried"] == ["AI tools for creators", "Claude AI"]
    ranked = cache["outliers"]
    assert [r["video_id"] for r in ranked] == ["b", "a", "c"]
    # 'b' kept the 8.0 multiple, not 5.0
    assert ranked[0]["outlier_multiple"] == 8.0
    assert "refreshed_at" in cache


def test_build_outliers_cache_top_n_caps_total() -> None:
    per_kw = {"x": [_o(str(i), float(i + 1)) for i in range(10)]}
    cache = yo.build_outliers_cache(per_kw, top_n_cap=3)
    assert len(cache["outliers"]) == 3
    assert [o["video_id"] for o in cache["outliers"]] == ["9", "8", "7"]
