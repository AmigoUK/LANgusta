"""LANgusta Textual App.

M1 ships the inventory screen. Later milestones add: asset detail (M3),
universal search (M4), review queue (M4), monitor config (M7).
"""

from __future__ import annotations

from textual.app import App
from textual.binding import Binding

from langusta.tui.screens.inventory import InventoryScreen


class LangustaApp(App):
    """Root Textual application."""

    CSS_PATH = "styles.tcss"
    TITLE = "LANgusta"
    SUB_TITLE = "asset registry · network scanner · lightweight monitoring"

    BINDINGS = (
        Binding("q", "quit", "Quit", priority=True),
    )

    def on_mount(self) -> None:
        self.push_screen(InventoryScreen())
