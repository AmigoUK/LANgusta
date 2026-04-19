"""Widget-level test — HeartbeatBar.refresh_now() reads the DB and updates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.tui.widgets.heartbeat import HeartbeatBar


def _home(tmp_path: Path, heartbeat: datetime | None) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    langusta_dir = home / ".langusta"
    langusta_dir.mkdir()
    db = langusta_dir / "db.sqlite"
    migrate(db)
    if heartbeat is not None:
        with connect(db) as conn:
            mon_dal.set_heartbeat(conn, now=heartbeat)
    return home


class _HarnessApp(App):
    def compose(self) -> ComposeResult:
        yield HeartbeatBar()


@pytest.mark.asyncio
async def test_widget_state_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(_home(tmp_path, heartbeat=None)))
    async with _HarnessApp().run_test() as pilot:
        bar = pilot.app.query_one(HeartbeatBar)
        bar.refresh_now()
        assert bar.state == "never"
        assert bar.has_class("-never")


@pytest.mark.asyncio
async def test_widget_state_fresh_when_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hb = datetime.now(UTC) - timedelta(seconds=5)
    monkeypatch.setenv("HOME", str(_home(tmp_path, heartbeat=hb)))
    async with _HarnessApp().run_test() as pilot:
        bar = pilot.app.query_one(HeartbeatBar)
        bar.refresh_now()
        assert bar.state == "fresh"
        assert bar.has_class("-fresh")


@pytest.mark.asyncio
async def test_widget_state_stale_when_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hb = datetime.now(UTC) - timedelta(minutes=30)
    monkeypatch.setenv("HOME", str(_home(tmp_path, heartbeat=hb)))
    async with _HarnessApp().run_test() as pilot:
        bar = pilot.app.query_one(HeartbeatBar)
        bar.refresh_now()
        assert bar.state == "stale"
        assert bar.has_class("-stale")


@pytest.mark.asyncio
async def test_widget_transitions_on_second_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First refresh finds nothing; after a write, second refresh sees fresh."""
    home = _home(tmp_path, heartbeat=None)
    monkeypatch.setenv("HOME", str(home))
    async with _HarnessApp().run_test() as pilot:
        bar = pilot.app.query_one(HeartbeatBar)
        bar.refresh_now()
        assert bar.state == "never"

        with connect(home / ".langusta" / "db.sqlite") as conn:
            mon_dal.set_heartbeat(conn, now=datetime.now(UTC) - timedelta(seconds=2))
        bar.refresh_now()
        assert bar.state == "fresh"
