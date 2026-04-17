"""Universal search screen.

Opens on `/` from any other screen. Live-filters asset rows as the user
types. Enter on a result pushes the asset detail screen; Esc pops back.

Spec: docs/specs/01-functionality-and-moscow.md §2 (incident-mode).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static

from langusta import paths
from langusta.core.models import Asset
from langusta.db import search as search_dal
from langusta.db.connection import connect


class SearchScreen(Screen):
    """Fuzzy search across asset text fields + MACs."""

    BINDINGS = (
        Binding("escape", "app.pop_screen", "Back", priority=True),
        Binding("enter", "open_selected", "Open", priority=False),
    )

    DEFAULT_CSS = """
    SearchScreen { align: center top; }
    #search_input { dock: top; margin: 1 0; }
    #hint { padding: 0 1; color: $text-muted; }
    DataTable { height: 1fr; }
    """

    def __init__(self, *, initial_query: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._initial_query = initial_query

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(
            placeholder="Type to search hostname, IP, MAC, description, notes…",
            id="search_input",
            value=self._initial_query,
        )
        with Vertical():
            yield Static(
                "Type to search. Enter to open, Esc to go back.",
                id="hint",
            )
            yield DataTable(cursor_type="row", id="results")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("ID", "Hostname", "IP", "MAC", "Description")
        # Seed initial results if a query was pre-filled (for snapshot tests
        # and `/` typed mid-flight from inventory with partial text).
        if self._initial_query:
            self._refresh(self._initial_query)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def _refresh(self, query: str) -> None:
        table = self.query_one(DataTable)
        table.clear()
        if not query.strip():
            return
        with connect(paths.db_path()) as conn:
            results: list[Asset] = search_dal.search(conn, query)
        for asset in results:
            table.add_row(
                str(asset.id),
                asset.hostname or "-",
                asset.primary_ip or "-",
                ",".join(asset.macs) if asset.macs else "-",
                (asset.description or "-")[:40],
            )

    def action_open_selected(self) -> None:
        """Push the asset detail for the currently-selected row."""
        from langusta.tui.screens.asset_detail import AssetDetailScreen

        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return
        row = table.get_row(row_key)
        asset_id = int(row[0])
        self.app.pop_screen()
        self.app.push_screen(AssetDetailScreen(asset_id=asset_id))
