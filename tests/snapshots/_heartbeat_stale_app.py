"""Harness app: HeartbeatBar frozen on a `stale` reading.

We subclass the widget and override `_read_display` with a fixed
`HeartbeatDisplay` so the snapshot doesn't drift with wall time —
the production widget computes "Ns ago" off `datetime.now(UTC)` which
would make any SVG baseline flaky.
"""
from __future__ import annotations

from textual.app import App, ComposeResult

from langusta.tui.widgets.heartbeat import HeartbeatBar, HeartbeatDisplay


class _FrozenHeartbeatBar(HeartbeatBar):
    def _read_display(self) -> HeartbeatDisplay:
        return HeartbeatDisplay(
            marker="🟡",
            text="monitor: stale (3m 15s ago)",
            state="stale",
        )


class _App(App):
    TITLE = "heartbeat-stale"

    def compose(self) -> ComposeResult:
        yield _FrozenHeartbeatBar()


if __name__ == "__main__":
    _App().run()
else:
    app = _App()
