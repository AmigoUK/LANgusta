"""Journal editor modal screen.

Press `n` on the asset detail screen to open. Save writes a new `note`
timeline entry with `author='user'`. Timeline entries are immutable —
this is the sanctioned way to record a change, a fix, or a runbook note.
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static, TextArea

from langusta import paths
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect


class JournalEditorScreen(ModalScreen):
    """Modal markdown editor for a new timeline note."""

    BINDINGS = (
        Binding("escape", "app.pop_screen", "Cancel", priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
    )

    DEFAULT_CSS = """
    JournalEditorScreen {
        align: center middle;
    }
    #editor_box {
        width: 80%;
        height: 80%;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    #editor_title { text-style: bold; padding-bottom: 1; }
    #editor_hint  { color: $text-muted; padding-top: 1; }
    TextArea { height: 1fr; }
    """

    def __init__(self, asset_id: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._asset_id = asset_id

    def compose(self) -> ComposeResult:
        with Vertical(id="editor_box"):
            yield Static(f"New note for asset #{self._asset_id}", id="editor_title")
            yield TextArea.code_editor(language="markdown", id="note_body")
            yield Static(
                "Ctrl+S to save · Esc to cancel · entries are append-only",
                id="editor_hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_save(self) -> None:
        body = self.query_one(TextArea).text.strip()
        if not body:
            # Empty save = cancel.
            self.app.pop_screen()
            return
        now = datetime.now(UTC)
        with connect(paths.db_path()) as conn:
            tl_dal.append_entry(
                conn,
                asset_id=self._asset_id,
                kind="note",
                body=body,
                now=now,
                author="user",
            )
        self.app.pop_screen()
