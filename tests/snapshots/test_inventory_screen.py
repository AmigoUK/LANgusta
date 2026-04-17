"""Textual snapshot tests for the inventory screen.

Run with `--snapshot-update` when the UI intentionally changes, then commit
the regenerated `.svg` under tests/snapshots/__snapshots__/.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

APP_SCRIPT = Path(__file__).parent / "_inventory_app.py"


def _seeded_home(tmp_path: Path, seed_assets: list[dict] | None) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    langusta_dir = home / ".langusta"
    langusta_dir.mkdir()
    db = langusta_dir / "db.sqlite"
    migrate(db)
    if seed_assets:
        now = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
        with connect(db) as conn:
            for seed in seed_assets:
                assets_dal.insert_manual(conn, now=now, **seed)
    return home


@pytest.fixture
def empty_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = _seeded_home(tmp_path, seed_assets=None)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def one_asset_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = _seeded_home(
        tmp_path,
        seed_assets=[
            {
                "hostname": "router",
                "primary_ip": "192.168.1.1",
                "mac": "aa:bb:cc:dd:ee:ff",
                "description": "core router",
            }
        ],
    )
    monkeypatch.setenv("HOME", str(home))
    return home


def test_inventory_empty(snap_compare, empty_home: Path) -> None:
    """Empty-state inventory renders a friendly 'no assets' message."""
    assert snap_compare(str(APP_SCRIPT), terminal_size=(100, 24))


def test_inventory_one_asset(snap_compare, one_asset_home: Path) -> None:
    """One-asset inventory shows the DataTable row with the seeded host."""
    assert snap_compare(str(APP_SCRIPT), terminal_size=(100, 24))
