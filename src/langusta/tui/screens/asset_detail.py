"""Asset detail screen — identity fields + immutable timeline.

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar A, §5 Flow 2.

M3 renders fields + timeline. Journal editor (press `n`) lands when the
input-modal story settles; the timeline widget already displays notes
written by CLI or direct DAL.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from langusta import paths
from langusta.db import assets as assets_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.tui.widgets.timeline import TimelineWidget


class AssetDetailScreen(Screen):
    """Detail view for one asset."""

    BINDINGS = (
        Binding("q", "app.pop_screen", "Back", priority=True),
        Binding("n", "new_note", "New note", priority=False),
    )

    DEFAULT_CSS = """
    AssetDetailScreen { align: center top; }
    #asset_identity { border: round $primary; padding: 0 1; margin: 1 0; }
    #asset_fields { padding: 0 1; }
    #timeline_title { padding: 1 1 0 1; text-style: bold; }
    .muted { color: $text-muted; }
    """

    def __init__(self, asset_id: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._asset_id = asset_id

    def compose(self) -> ComposeResult:
        yield Header()
        with connect(paths.db_path()) as conn:
            asset = assets_dal.get_by_id(conn, self._asset_id)
            entries = tl_dal.list_by_asset(conn, self._asset_id)

        if asset is None:
            yield Static(f"Asset #{self._asset_id} not found.", classes="muted")
            yield Footer()
            return

        identity = (
            f"#{asset.id}  {asset.hostname or '(no hostname)'}\n"
            f"IP: {asset.primary_ip or '-'}    "
            f"MAC: {', '.join(asset.macs) if asset.macs else '-'}    "
            f"Vendor: {asset.vendor or '-'}"
        )
        yield Static(identity, id="asset_identity")

        field_lines = []
        for label, value in (
            ("Description", asset.description),
            ("Location", asset.location),
            ("Owner", asset.owner),
            ("Criticality", asset.criticality),
            ("Management URL", asset.management_url),
            ("OS", asset.detected_os),
            ("Device type", asset.device_type),
            ("Source", asset.source),
            ("First seen", asset.first_seen.strftime("%Y-%m-%d %H:%M")),
            ("Last seen", asset.last_seen.strftime("%Y-%m-%d %H:%M")),
        ):
            if value:
                field_lines.append(f"[bold]{label}:[/bold] {value}")
        if field_lines:
            yield Static("\n".join(field_lines), id="asset_fields")

        yield Static("Timeline", id="timeline_title")
        with Vertical():
            yield TimelineWidget(entries)

        yield Footer()

    def action_new_note(self) -> None:
        from langusta.tui.screens.journal_editor import JournalEditorScreen

        self.app.push_screen(JournalEditorScreen(asset_id=self._asset_id))
