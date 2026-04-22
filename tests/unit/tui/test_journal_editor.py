"""JournalEditorScreen pilot tests — Wave-3 TEST-T-009.

Verifies:
  - Ctrl+S with typed content appends exactly one `note` timeline entry
    with the typed body and `author='user'`.
  - Ctrl+S with an empty body is a no-op (treated as cancel) and writes
    nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import TextArea

from langusta.db import assets as assets_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.tui.screens.journal_editor import JournalEditorScreen


class _Host(App):
    def __init__(self, asset_id: int) -> None:
        super().__init__()
        self._asset_id = asset_id

    def on_mount(self) -> None:
        self.push_screen(JournalEditorScreen(asset_id=self._asset_id))


@pytest.fixture
def db_with_asset(tmp_langusta_home: Path) -> tuple[Path, int]:
    from datetime import UTC, datetime

    langusta_dir = tmp_langusta_home
    langusta_dir.mkdir(parents=True, exist_ok=True)
    db = langusta_dir / "db.sqlite"
    migrate(db)
    with connect(db) as conn:
        asset_id = assets_dal.insert_manual(
            conn, hostname="r", primary_ip="10.0.0.1",
            now=datetime(2026, 4, 20, tzinfo=UTC),
        )
    return db, asset_id


@pytest.mark.asyncio
async def test_journal_editor_ctrl_s_writes_a_note_entry(
    db_with_asset: tuple[Path, int],
) -> None:
    """Type a body, press Ctrl+S, expect exactly one `note` entry on
    that asset's timeline with the typed body."""
    db, asset_id = db_with_asset

    async with _Host(asset_id).run_test() as pilot:
        # Wait for the modal + its TextArea to mount.
        await pilot.pause()
        modal = pilot.app.screen
        textarea = modal.query_one(TextArea)
        textarea.text = "replaced PSU"
        await pilot.press("ctrl+s")
        await pilot.pause()

    with connect(db) as conn:
        entries = tl_dal.list_by_asset(conn, asset_id)
    notes = [e for e in entries if e.kind == "note"]
    assert len(notes) == 1, f"expected exactly one note, got {notes}"
    assert notes[0].body == "replaced PSU"
    assert notes[0].author == "user"


@pytest.mark.asyncio
async def test_journal_editor_empty_save_writes_nothing(
    db_with_asset: tuple[Path, int],
) -> None:
    """Ctrl+S on an empty TextArea closes the modal without appending
    anything — the editor treats an empty buffer as 'cancel'."""
    db, asset_id = db_with_asset

    async with _Host(asset_id).run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    with connect(db) as conn:
        notes = [
            e for e in tl_dal.list_by_asset(conn, asset_id) if e.kind == "note"
        ]
    assert notes == []
