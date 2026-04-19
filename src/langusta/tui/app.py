"""LANgusta Textual App.

M1 ships the inventory screen. Later milestones add: asset detail (M3),
universal search (M4), review queue (M4), monitor config (M7).

Optional `LANGUSTA_KEYBINDINGS=vim` layers vim-style navigation aliases
(j/k/g/G/ctrl+d/ctrl+u) on top of the defaults — see `keybindings.py`.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from langusta.tui.keybindings import active_preset_from_env
from langusta.tui.screens.inventory import InventoryScreen


class LangustaApp(App):
    """Root Textual application."""

    # Absolute so subclasses in other modules (e.g., test harness scripts)
    # still find the stylesheet.
    CSS_PATH = str(Path(__file__).parent / "styles.tcss")
    TITLE = "LANgusta"
    SUB_TITLE = "asset registry · network scanner · lightweight monitoring"

    BINDINGS = (
        Binding("q", "quit", "Quit", priority=True),
    )

    def on_mount(self) -> None:
        for preset_binding in active_preset_from_env():
            self.bind(
                preset_binding.key,
                preset_binding.action,
                description=preset_binding.description,
                show=preset_binding.show,
            )
        self.push_screen(InventoryScreen())
