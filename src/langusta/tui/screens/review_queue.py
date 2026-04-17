"""Review queue screen — resolve scanner-proposed changes.

Keybindings:
  a  Accept the highlighted proposal (scanner wins; provenance → SCANNED).
  r  Reject (human wins; asset stays as-is).
  q  Back.
"""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from langusta import paths
from langusta.db import proposed_changes as pc_dal
from langusta.db.connection import connect


class ReviewQueueScreen(Screen):
    BINDINGS = (
        Binding("q", "app.pop_screen", "Back", priority=True),
        Binding("a", "accept", "Accept", priority=False),
        Binding("r", "reject", "Reject", priority=False),
    )

    DEFAULT_CSS = """
    ReviewQueueScreen { align: center top; }
    #empty_hint { content-align: center middle; height: 1fr; color: $text-muted; }
    DataTable { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with connect(paths.db_path()) as conn:
            rows = pc_dal.list_open(conn)

        if not rows:
            yield Static(
                "No pending proposals.\n\n"
                "Scanner observations that conflict with manually-set fields "
                "land here. Run `langusta scan` to populate.",
                id="empty_hint",
            )
        else:
            table = DataTable(cursor_type="row", zebra_stripes=True)
            table.add_columns("ID", "Asset", "Field", "Current", "Proposed", "Source")
            for r in rows:
                table.add_row(
                    str(r.id),
                    f"#{r.asset_id}",
                    r.field,
                    r.current_value or "-",
                    r.proposed_value or "-",
                    r.current_provenance.value,
                )
            yield table
        yield Footer()

    def _selected_pc_id(self) -> int | None:
        try:
            table = self.query_one(DataTable)
        except Exception:
            return None
        if table.row_count == 0:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        row = table.get_row(row_key)
        return int(row[0])

    def action_accept(self) -> None:
        pc_id = self._selected_pc_id()
        if pc_id is None:
            return
        now = datetime.now(UTC)
        with connect(paths.db_path()) as conn:
            try:
                pc_dal.accept(conn, pc_id, now=now)
            except pc_dal.AlreadyResolvedError:
                return
        self.app.pop_screen()
        self.app.push_screen(ReviewQueueScreen())

    def action_reject(self) -> None:
        pc_id = self._selected_pc_id()
        if pc_id is None:
            return
        now = datetime.now(UTC)
        with connect(paths.db_path()) as conn:
            try:
                pc_dal.reject(conn, pc_id, now=now)
            except pc_dal.AlreadyResolvedError:
                return
        self.app.pop_screen()
        self.app.push_screen(ReviewQueueScreen())
