"""Timeline widget — renders a list of TimelineEntry rows chronologically."""

from __future__ import annotations

from collections.abc import Iterable

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from langusta.db.timeline import TimelineEntry

_KIND_MARKERS = {
    "note": "📝",
    "scan_diff": "🔎",
    "monitor_event": "🩺",
    "disposition": "⚖️",
    "correction": "✏️",
    "import": "📥",
    "system": "⚙️",
}


class TimelineWidget(Widget):
    """Lightweight timeline renderer — static at compose time for M3."""

    DEFAULT_CSS = """
    TimelineWidget {
        height: auto;
        min-height: 5;
    }
    """

    def __init__(self, entries: Iterable[TimelineEntry], **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries = list(entries)

    def compose(self) -> ComposeResult:
        if not self._entries:
            yield Static("(no timeline entries yet)", classes="muted")
            return
        table = Table.grid(padding=(0, 1))
        table.add_column()
        table.add_column()
        table.add_column()
        table.add_column()
        for entry in self._entries:
            marker = _KIND_MARKERS.get(entry.kind, "•")
            stamp = entry.occurred_at.strftime("%Y-%m-%d %H:%M")
            body = Text(entry.body)
            if entry.corrects_id is not None:
                body.append(f"  (corrects #{entry.corrects_id})", style="dim")
            table.add_row(marker, stamp, f"[{entry.kind}]", body)
        yield Static(table)
