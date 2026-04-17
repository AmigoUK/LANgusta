"""Snapshot tests for the review queue screen."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import scans as scans_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)

APP_SCRIPT = Path(__file__).parent / "_review_queue_app.py"


def _home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    (h / ".langusta").mkdir()
    migrate(h / ".langusta" / "db.sqlite")
    return h


def _write_app_script() -> None:
    APP_SCRIPT.write_text(
        "from langusta.tui.app import LangustaApp\n"
        "from langusta.tui.screens.review_queue import ReviewQueueScreen\n"
        "class TestApp(LangustaApp):\n"
        "    def on_mount(self):\n"
        "        self.push_screen(ReviewQueueScreen())\n"
        "app = TestApp()\n"
    )


def test_review_queue_empty(snap_compare, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    _write_app_script()
    assert snap_compare(str(APP_SCRIPT), terminal_size=(120, 24))


def test_review_queue_one_item(snap_compare, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="192.168.1.1", now=NOW,
        )
        sid = scans_dal.start_scan(conn, target="192.168.1.0/24", now=NOW)
        pc_dal.insert(
            conn, asset_id=aid, field="hostname",
            current_value="router", current_provenance=FieldProvenance.MANUAL,
            proposed_value="scanner-guess", observed_at=NOW, scan_id=sid,
        )
    _write_app_script()
    assert snap_compare(str(APP_SCRIPT), terminal_size=(120, 24))


def test_review_queue_many_items(snap_compare, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="192.168.1.1", now=NOW,
        )
        sid = scans_dal.start_scan(conn, target="192.168.1.0/24", now=NOW)
        for field, current, proposed in (
            ("hostname", "router", "cisco-1"),
            ("description", "core", "main router"),
            ("location", "rack 1", "rack 3"),
        ):
            pc_dal.insert(
                conn, asset_id=aid, field=field,
                current_value=current, current_provenance=FieldProvenance.MANUAL,
                proposed_value=proposed, observed_at=NOW, scan_id=sid,
            )
    _write_app_script()
    assert snap_compare(str(APP_SCRIPT), terminal_size=(120, 24))
