"""Harness app: HeartbeatBar frozen on a `fresh` reading.

Same trick as `_heartbeat_stale_app.py` — subclass and return a fixed
HeartbeatDisplay so the SVG baseline doesn't drift with wall time.
The existing `_heartbeat_fresh_app.py` is kept as-is because the
original `test_heartbeat_widget.py::test_heartbeat_never_run` only
uses it for the "never" state (heartbeat is None at render time).
"""
from __future__ import annotations

from textual.app import App, ComposeResult

from langusta.tui.widgets.heartbeat import HeartbeatBar, HeartbeatDisplay


class _FrozenHeartbeatBar(HeartbeatBar):
    def _read_display(self) -> HeartbeatDisplay:
        return HeartbeatDisplay(
            marker="🟢",
            text="monitor: fresh (12s ago)",
            state="fresh",
        )


class _App(App):
    TITLE = "heartbeat-fresh-frozen"

    def compose(self) -> ComposeResult:
        yield _FrozenHeartbeatBar()


if __name__ == "__main__":
    _App().run()
else:
    app = _App()
