"""Inventory screen — DataTable of all assets.

Bindings:
  enter  Open asset detail.
  /      Universal search.
  r      Review queue.
  q      Quit (inherited from app root).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from langusta import paths
from langusta.db import assets as assets_dal
from langusta.db.connection import connect


class InventoryScreen(Screen):
    """List every asset in the DB."""

    BINDINGS = (
        Binding("slash", "open_search", "Search", priority=False),
        Binding("r", "open_review", "Review", priority=False),
    )

    def compose(self) -> ComposeResult:
        yield Header()
        with connect(paths.db_path()) as conn:
            rows = assets_dal.list_all(conn)

        if not rows:
            yield Static(
                "No assets yet.\n\n"
                "Run `langusta add --hostname ... --ip ...` to create one, "
                "or `langusta scan <subnet>` to populate.",
                id="empty_state",
            )
        else:
            table = DataTable(cursor_type="row", zebra_stripes=True, id="inventory")
            table.add_columns("ID", "Hostname", "IP", "MAC", "Source", "Last seen")
            for asset in rows:
                table.add_row(
                    str(asset.id),
                    asset.hostname or "-",
                    asset.primary_ip or "-",
                    ",".join(asset.macs) if asset.macs else "-",
                    asset.source,
                    asset.last_seen.strftime("%Y-%m-%d %H:%M"),
                )
            yield table

        yield Footer()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        from langusta.tui.screens.asset_detail import AssetDetailScreen

        row = self.query_one(DataTable).get_row(event.row_key)
        asset_id = int(row[0])
        self.app.push_screen(AssetDetailScreen(asset_id=asset_id))

    def action_open_search(self) -> None:
        from langusta.tui.screens.search import SearchScreen

        self.app.push_screen(SearchScreen())

    def action_open_review(self) -> None:
        from langusta.tui.screens.review_queue import ReviewQueueScreen

        self.app.push_screen(ReviewQueueScreen())
