"""Harness app: TimelineWidget rendered with a mixed-kind set of entries.

Snapshot baseline for TEST-T-010 (Wave-3, medium). The entries use a
fixed-in-time `occurred_at` so the SVG is deterministic across runs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import App, ComposeResult

from langusta.db.timeline import TimelineEntry
from langusta.tui.widgets.timeline import TimelineWidget

_STAMP = datetime(2026, 4, 20, 9, 0, 0, tzinfo=UTC)


def _entries() -> list[TimelineEntry]:
    return [
        TimelineEntry(
            id=1, asset_id=1, kind="system",
            body="Asset discovered at 10.0.0.1.",
            occurred_at=_STAMP, author="scanner", corrects_id=None,
        ),
        TimelineEntry(
            id=2, asset_id=1, kind="scan_diff",
            body="Open ports: 22, 80, 443",
            occurred_at=_STAMP.replace(hour=10), author="scanner",
            corrects_id=None,
        ),
        TimelineEntry(
            id=3, asset_id=1, kind="note",
            body="Owner confirmed: ops team.",
            occurred_at=_STAMP.replace(hour=11), author="user",
            corrects_id=None,
        ),
        TimelineEntry(
            id=4, asset_id=1, kind="monitor_event",
            body="ICMP failure (timeout)",
            occurred_at=_STAMP.replace(hour=12), author="monitor",
            corrects_id=None,
        ),
        TimelineEntry(
            id=5, asset_id=1, kind="correction",
            body="Hostname corrected: router-1 -> router-core.",
            occurred_at=_STAMP.replace(hour=13), author="user",
            corrects_id=3,
        ),
    ]


class _App(App):
    TITLE = "timeline-mixed"

    def compose(self) -> ComposeResult:
        yield TimelineWidget(_entries())


if __name__ == "__main__":
    _App().run()
else:
    app = _App()
