"""Pure formatter tests for the heartbeat bar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langusta.tui.widgets.heartbeat import format_heartbeat

NOW = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)


def test_no_heartbeat_is_never() -> None:
    d = format_heartbeat(None, now=NOW)
    assert d.state == "never"
    assert d.marker == "⚫"
    assert "never run" in d.text


def test_recent_heartbeat_is_fresh() -> None:
    d = format_heartbeat(NOW - timedelta(seconds=15), now=NOW)
    assert d.state == "fresh"
    assert d.marker == "🟢"
    assert "15s ago" in d.text


def test_just_under_tolerance_is_fresh() -> None:
    d = format_heartbeat(NOW - timedelta(seconds=119), now=NOW, tolerance_seconds=120)
    assert d.state == "fresh"
    assert "1m 59s" in d.text


def test_over_tolerance_is_stale() -> None:
    d = format_heartbeat(NOW - timedelta(seconds=300), now=NOW, tolerance_seconds=120)
    assert d.state == "stale"
    assert d.marker == "🟡"
    assert "5m ago" in d.text


def test_hours_ago_stale_formatting() -> None:
    d = format_heartbeat(NOW - timedelta(hours=2, minutes=13), now=NOW)
    assert d.state == "stale"
    assert "2h 13m" in d.text


def test_exact_tolerance_boundary_is_fresh() -> None:
    """At exactly the tolerance boundary the daemon is still considered fresh."""
    d = format_heartbeat(NOW - timedelta(seconds=120), now=NOW, tolerance_seconds=120)
    assert d.state == "fresh"


def test_custom_tolerance_overrides_default() -> None:
    # 30s tolerance: 45s ago is stale.
    d = format_heartbeat(NOW - timedelta(seconds=45), now=NOW, tolerance_seconds=30)
    assert d.state == "stale"


def test_future_heartbeat_clamps_to_zero_age() -> None:
    """Clock skew shouldn't produce negative ages."""
    d = format_heartbeat(NOW + timedelta(seconds=5), now=NOW)
    assert d.state == "fresh"
    assert "0s" in d.text
