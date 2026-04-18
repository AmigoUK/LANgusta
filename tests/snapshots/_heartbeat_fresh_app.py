"""Harness app: HeartbeatBar with a fresh heartbeat seeded in the DB."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer

from langusta.tui.widgets.heartbeat import HeartbeatBar


class _App(App):
    TITLE = "heartbeat-fresh"

    def compose(self) -> ComposeResult:
        yield HeartbeatBar()
        yield Footer()


if __name__ == "__main__":
    _App().run()
else:
    app = _App()
