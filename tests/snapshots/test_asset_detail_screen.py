"""Asset detail screen snapshot tests (M3).

Three states:
  - empty timeline (just the 'discovered' system entry)
  - timeline with one scan_diff and one note
  - timeline with a correction

The snapshot plugin drives each app variant via a small helper script at
_asset_detail_app_{N}.py so each snapshot has a stable, deterministic DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from langusta.db import assets as assets_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".langusta").mkdir()
    migrate(home / ".langusta" / "db.sqlite")
    return home


def test_asset_detail_empty_timeline(snap_compare, tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="192.168.1.1",
            mac="aa:bb:cc:dd:ee:ff", description="core router",
            vendor="Cisco", location="rack 1", now=NOW,
        )
    app_py = Path(__file__).parent / "_asset_detail_app.py"
    app_py.write_text(
        "from langusta.tui.app import LangustaApp\n"
        "from langusta.tui.screens.asset_detail import AssetDetailScreen\n"
        f"AID = {aid}\n"
        "class TestApp(LangustaApp):\n"
        "    def on_mount(self):\n"
        "        self.push_screen(AssetDetailScreen(asset_id=AID))\n"
        "app = TestApp()\n"
    )
    assert snap_compare(str(app_py), terminal_size=(100, 30))


def test_asset_detail_with_scan_diff_and_note(snap_compare, tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="192.168.1.1",
            mac="aa:bb:cc:dd:ee:ff", vendor="Cisco", now=NOW,
        )
        tl_dal.append_entry(
            conn, asset_id=aid, kind="scan_diff",
            body="Open ports: 22, 80, 443",
            now=NOW, author="scanner",
        )
        tl_dal.append_entry(
            conn, asset_id=aid, kind="note",
            body="Replaced PSU after grinding noise.",
            now=NOW, author="admin",
        )
    app_py = Path(__file__).parent / "_asset_detail_app.py"
    app_py.write_text(
        "from langusta.tui.app import LangustaApp\n"
        "from langusta.tui.screens.asset_detail import AssetDetailScreen\n"
        f"AID = {aid}\n"
        "class TestApp(LangustaApp):\n"
        "    def on_mount(self):\n"
        "        self.push_screen(AssetDetailScreen(asset_id=AID))\n"
        "app = TestApp()\n"
    )
    assert snap_compare(str(app_py), terminal_size=(100, 30))


def test_asset_detail_with_correction_entry(snap_compare, tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        aid = assets_dal.insert_manual(conn, hostname="router", primary_ip="10.0.0.1", now=NOW)
        orig = tl_dal.append_entry(
            conn, asset_id=aid, kind="note",
            body="Wrong serial: ABCD1111", now=NOW, author="admin",
        )
        tl_dal.append_correction_of(
            conn, original_id=orig,
            body="Actually serial is ABCD9999.", now=NOW, author="admin",
        )
    app_py = Path(__file__).parent / "_asset_detail_app.py"
    app_py.write_text(
        "from langusta.tui.app import LangustaApp\n"
        "from langusta.tui.screens.asset_detail import AssetDetailScreen\n"
        f"AID = {aid}\n"
        "class TestApp(LangustaApp):\n"
        "    def on_mount(self):\n"
        "        self.push_screen(AssetDetailScreen(asset_id=AID))\n"
        "app = TestApp()\n"
    )
    assert snap_compare(str(app_py), terminal_size=(100, 30))
