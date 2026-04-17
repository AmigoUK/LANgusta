"""Inventory screen — DataTable of all assets.

The screen is the TUI front door for M1. It reads from `db.assets.list_all`
and renders one row per asset. Empty state shows a hint.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from langusta import paths
from langusta.db import assets as assets_dal
from langusta.db.connection import connect


class InventoryScreen(Screen):
    """List every asset in the DB."""

    def compose(self) -> ComposeResult:
        yield Header()
        with connect(paths.db_path()) as conn:
            rows = assets_dal.list_all(conn)

        if not rows:
            yield Static(
                "No assets yet.\n\n"
                "Run `langusta add --hostname ... --ip ...` to create one, "
                "or `langusta scan <subnet>` once M2 lands.",
                id="empty_state",
            )
        else:
            table = DataTable(cursor_type="row", zebra_stripes=True)
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
