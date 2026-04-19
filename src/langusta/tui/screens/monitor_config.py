"""Monitor config screen — view and toggle monitoring checks.

Keybindings:
  e   Toggle enabled state of highlighted check.
  q   Back.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from langusta import paths
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.tui.widgets.heartbeat import HeartbeatBar


def _target_summary(check: mon_dal.MonitoringCheck) -> str:
    parts: list[str] = []
    if check.target:
        parts.append(check.target)
    if check.port is not None:
        parts.append(f":{check.port}")
    if check.path:
        parts.append(check.path)
    if check.oid:
        parts.append(f"oid={check.oid}")
    if check.command:
        parts.append(f"cmd={check.command!r}")
    return "".join(parts) if parts else "-"


class MonitorConfigScreen(Screen):
    """List configured monitoring checks; toggle enabled state."""

    BINDINGS = (
        Binding("q", "app.pop_screen", "Back", priority=True),
        Binding("e", "toggle_enabled", "Toggle", priority=False),
    )

    DEFAULT_CSS = """
    MonitorConfigScreen { align: center top; }
    #empty_hint { content-align: center middle; height: 1fr; color: $text-muted; }
    DataTable { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with connect(paths.db_path()) as conn:
            rows = mon_dal.list_checks(conn)

        if not rows:
            yield Static(
                "No monitoring checks configured.\n\n"
                "Run `langusta monitor enable --asset ID --kind icmp` "
                "(or tcp/http/snmp_oid/ssh_command) to configure one.",
                id="empty_hint",
            )
        else:
            table = DataTable(cursor_type="row", zebra_stripes=True, id="checks")
            table.add_columns(
                "ID", "Asset", "Kind", "Target", "Every", "Status", "Last",
            )
            for c in rows:
                table.add_row(
                    str(c.id),
                    f"#{c.asset_id}",
                    c.kind,
                    _target_summary(c),
                    f"{c.interval_seconds}s",
                    "enabled" if c.enabled else "disabled",
                    c.last_status or "-",
                )
            yield table

        yield HeartbeatBar()
        yield Footer()

    def _selected_check_id(self) -> int | None:
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

    def action_toggle_enabled(self) -> None:
        check_id = self._selected_check_id()
        if check_id is None:
            return
        with connect(paths.db_path()) as conn:
            check = mon_dal.get_by_id(conn, check_id)
            if check is None:
                return
            mon_dal.set_check_enabled(conn, check_id, enabled=not check.enabled)
        # Re-push to refresh the table.
        self.app.pop_screen()
        self.app.push_screen(MonitorConfigScreen())
