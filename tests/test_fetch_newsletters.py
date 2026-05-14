"""TDD for pipeline.fetch.newsletters — RSS cross-mention aggregator."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pipeline.fetch import newsletters

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_parse_feed_extracts_recent_entries() -> None:
    xml = (FIXTURES / "newsletter_a.xml").read_text(encoding="utf-8")
    entries = newsletters.parse_feed(xml)
    assert len(entries) == 2
    assert entries[0].link == "https://newsletter-a.example.com/issue-42"


def test_extract_urls_from_html_picks_up_anchors_and_bare_urls() -> None:
    html = (
        '<a href="https://example.com/a">A</a>'
        " plus https://example.com/b in plain text."
    )
    urls = newsletters.extract_urls_from_html(html)
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls


def test_aggregate_cross_mentions_counts_unique_newsletters() -> None:
    xml_a = (FIXTURES / "newsletter_a.xml").read_text(encoding="utf-8")
    xml_b = (FIXTURES / "newsletter_b.xml").read_text(encoding="utf-8")
    today = datetime(2026, 5, 13, 23, 0, tzinfo=timezone.utc)
    signals = newsletters.aggregate_from_xml(
        feeds={"Newsletter A": xml_a, "Newsletter B": xml_b},
        today=today,
        lookback_days=14,
    )
    by_url = {s.url: s for s in signals}
    # MCP and latent-space links appear in both feeds.
    mcp = by_url["https://github.com/anthropic/mcp"]
    assert mcp.unique_newsletters_count == 2
    assert set(mcp.newsletters) == {"Newsletter A", "Newsletter B"}
    latent = by_url["https://www.latent.space/p/world-model-agents"]
    assert latent.unique_newsletters_count == 2


def test_aggregate_drops_old_entries() -> None:
    xml_a = (FIXTURES / "newsletter_a.xml").read_text(encoding="utf-8")
    # Today set so only issue-42 is inside the 14d window — issue-41 (May 6) too.
    # Force the test by setting today far in the future.
    today = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    signals = newsletters.aggregate_from_xml(
        feeds={"Newsletter A": xml_a}, today=today, lookback_days=14
    )
    # Nothing recent → no signals.
    assert signals == []


def test_signal_carries_first_and_last_seen() -> None:
    xml_a = (FIXTURES / "newsletter_a.xml").read_text(encoding="utf-8")
    xml_b = (FIXTURES / "newsletter_b.xml").read_text(encoding="utf-8")
    today = datetime(2026, 5, 13, 23, 0, tzinfo=timezone.utc)
    signals = newsletters.aggregate_from_xml(
        feeds={"Newsletter A": xml_a, "Newsletter B": xml_b},
        today=today,
        lookback_days=14,
    )
    by_url = {s.url: s for s in signals}
    mcp = by_url["https://github.com/anthropic/mcp"]
    assert mcp.first_seen <= mcp.last_seen
