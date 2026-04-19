"""TUI heartbeat bar — daemon-freshness indicator shown above the footer.

Reads `meta.daemon_heartbeat` and renders one of three states:

    🟢 monitor: fresh (12s ago)
    🟡 monitor: stale (3m 15s ago)
    ⚫ monitor: never run

The pure formatter lives in this module too so it can be unit-tested
without spinning a Textual app.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from textual.reactive import reactive
from textual.widgets import Static

from langusta import paths
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect

_REFRESH_INTERVAL_SECONDS = 15.0
_DEFAULT_TOLERANCE_SECONDS = 120


@dataclass(frozen=True, slots=True)
class HeartbeatDisplay:
    """Pure value describing what the heartbeat bar should render."""

    marker: str   # 🟢 | 🟡 | ⚫
    text: str     # human-readable phrase
    state: str    # 'fresh' | 'stale' | 'never'


def format_heartbeat(
    heartbeat: datetime | None,
    *,
    now: datetime,
    tolerance_seconds: int = _DEFAULT_TOLERANCE_SECONDS,
) -> HeartbeatDisplay:
    if heartbeat is None:
        return HeartbeatDisplay(marker="⚫", text="monitor: never run", state="never")
    delta = now - heartbeat
    age_str = _format_age(delta)
    if delta <= timedelta(seconds=tolerance_seconds):
        return HeartbeatDisplay(
            marker="🟢",
            text=f"monitor: fresh ({age_str} ago)",
            state="fresh",
        )
    return HeartbeatDisplay(
        marker="🟡",
        text=f"monitor: stale ({age_str} ago)",
        state="stale",
    )


def _format_age(delta: timedelta) -> str:
    total = max(int(delta.total_seconds()), 0)
    if total < 60:
        return f"{total}s"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


class HeartbeatBar(Static):
    """One-line heartbeat indicator; refreshes every 15 seconds.

    Fetches `meta.daemon_heartbeat` from the DB. To keep the widget
    testable and offline-friendly, the refresh path is a plain method
    (`refresh_now`) that a caller can invoke manually in tests.
    """

    DEFAULT_CSS = """
    HeartbeatBar {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    HeartbeatBar.-fresh { color: $success; }
    HeartbeatBar.-stale { color: $warning; text-style: bold; }
    HeartbeatBar.-never { color: $text-muted; }
    """

    state: reactive[str] = reactive("never")

    def __init__(self, *, tolerance_seconds: int = _DEFAULT_TOLERANCE_SECONDS) -> None:
        self._tolerance_seconds = tolerance_seconds
        # Initial render happens before mount so the first frame isn't blank.
        display = self._read_display()
        super().__init__(
            f"{display.marker}  {display.text}",
            id="heartbeat_bar",
            classes=f"-{display.state}",
        )
        self._initial_state = display.state

    def on_mount(self) -> None:
        self.set_interval(_REFRESH_INTERVAL_SECONDS, self.refresh_now)

    def refresh_now(self) -> None:
        self._apply(self._read_display())

    def _read_display(self) -> HeartbeatDisplay:
        try:
            with connect(paths.db_path()) as conn:
                hb = mon_dal.get_heartbeat(conn)
        except Exception:
            hb = None
        return format_heartbeat(
            hb, now=datetime.now(UTC),
            tolerance_seconds=self._tolerance_seconds,
        )

    def _apply(self, display: HeartbeatDisplay) -> None:
        self.state = display.state
        # Swap the state class so CSS colour follows the reactive.
        for cls in ("-fresh", "-stale", "-never"):
            self.remove_class(cls)
        self.add_class(f"-{display.state}")
        self.update(f"{display.marker}  {display.text}")
