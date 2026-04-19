"""Widget-level tests — MonitorConfigScreen lists checks and toggles enabled state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.widgets import DataTable, Static

from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.tui.app import LangustaApp
from langusta.tui.screens.monitor_config import MonitorConfigScreen

NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    langusta_dir = home / ".langusta"
    langusta_dir.mkdir()
    migrate(langusta_dir / "db.sqlite")
    return home


async def _open_monitor_screen(pilot) -> MonitorConfigScreen:
    """Drive the app from InventoryScreen to MonitorConfigScreen via the 'm' binding."""
    await pilot.pause()
    await pilot.press("m")
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, MonitorConfigScreen)
    return screen


@pytest.mark.asyncio
async def test_empty_state_when_no_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(_home(tmp_path)))
    async with LangustaApp().run_test() as pilot:
        screen = await _open_monitor_screen(pilot)
        hint = screen.query_one("#empty_hint", Static)
        assert "No monitoring checks" in hint.render().plain


@pytest.mark.asyncio
async def test_lists_configured_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    with connect(home / ".langusta" / "db.sqlite") as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1", now=NOW,
        )
        mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60,
            target="10.0.0.1", now=NOW,
        )
        mon_dal.enable_check(
            conn, asset_id=aid, kind="http", interval_seconds=300,
            port=443, path="/healthz", now=NOW,
        )
    async with LangustaApp().run_test() as pilot:
        screen = await _open_monitor_screen(pilot)
        table = screen.query_one(DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_toggle_enabled_flips_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    db = home / ".langusta" / "db.sqlite"
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1", now=NOW,
        )
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )

    async with LangustaApp().run_test() as pilot:
        await _open_monitor_screen(pilot)
        await pilot.press("e")
        await pilot.pause()

    with connect(db) as conn:
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.enabled is False

    async with LangustaApp().run_test() as pilot:
        await _open_monitor_screen(pilot)
        await pilot.press("e")
        await pilot.pause()

    with connect(db) as conn:
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.enabled is True


@pytest.mark.asyncio
async def test_toggle_with_no_rows_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(_home(tmp_path)))
    async with LangustaApp().run_test() as pilot:
        await _open_monitor_screen(pilot)
        # Should not raise.
        await pilot.press("e")
        await pilot.pause()


