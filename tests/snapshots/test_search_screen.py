"""Snapshot tests for the universal search screen.

Empty state: just the input placeholder.
One match:  one-row result list.
Many matches: ranked result list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    (h / ".langusta").mkdir()
    migrate(h / ".langusta" / "db.sqlite")
    return h


def _make_app_py(tmp_path: Path, initial_query: str) -> Path:
    script = Path(__file__).parent / "_search_app.py"
    script.write_text(
        "from langusta.tui.app import LangustaApp\n"
        "from langusta.tui.screens.search import SearchScreen\n"
        f"INITIAL = {initial_query!r}\n"
        "class TestApp(LangustaApp):\n"
        "    def on_mount(self):\n"
        "        self.push_screen(SearchScreen(initial_query=INITIAL))\n"
        "app = TestApp()\n"
    )
    return script


def test_search_screen_empty(snap_compare, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    script = _make_app_py(tmp_path, "")
    assert snap_compare(str(script), terminal_size=(100, 24))


def test_search_screen_one_match(snap_compare, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        assets_dal.insert_manual(
            conn, hostname="router-core", primary_ip="192.168.1.1",
            description="reception switch", now=NOW,
        )
        assets_dal.insert_manual(
            conn, hostname="printer-marketing", primary_ip="192.168.1.50",
            description="HP LaserJet", now=NOW,
        )
    script = _make_app_py(tmp_path, "reception")
    assert snap_compare(str(script), terminal_size=(100, 24))


def test_search_screen_many_matches_ranked(snap_compare, tmp_path, monkeypatch) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        for i in range(5):
            assets_dal.insert_manual(
                conn, hostname=f"printer-{i:02d}",
                primary_ip=f"10.0.0.{10 + i}",
                description="HP LaserJet M428",
                now=NOW,
            )
    script = _make_app_py(tmp_path, "printer")
    assert snap_compare(str(script), terminal_size=(100, 24))
