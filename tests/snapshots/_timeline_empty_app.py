"""Harness app: TimelineWidget rendered with zero entries.

Snapshot baseline for TEST-T-010 (Wave-3, medium) — exercises the
'(no timeline entries yet)' muted placeholder branch.
"""

from __future__ import annotations

from textual.app import App, ComposeResult

from langusta.tui.widgets.timeline import TimelineWidget


class _App(App):
    TITLE = "timeline-empty"

    def compose(self) -> ComposeResult:
        yield TimelineWidget([])


if __name__ == "__main__":
    _App().run()
else:
    app = _App()
