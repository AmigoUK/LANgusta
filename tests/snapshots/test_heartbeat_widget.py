"""Snapshot tests for HeartbeatBar.

The never-run state is time-invariant. Fresh and stale states get
snapshot coverage via harness apps that subclass the widget and
freeze the `_read_display` output to a fixed HeartbeatDisplay, so
the rendered "Ns ago" string doesn't drift with wall time. The pure
formatter itself is covered by `tests/unit/tui/test_heartbeat_format.py`.
Wave-3 TEST-T-020.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

APP_SCRIPT = Path(__file__).parent / "_heartbeat_fresh_app.py"
APP_SCRIPT_FRESH_FROZEN = Path(__file__).parent / "_heartbeat_fresh_frozen_app.py"
APP_SCRIPT_STALE = Path(__file__).parent / "_heartbeat_stale_app.py"


def _home_with_heartbeat(tmp_path: Path, heartbeat: datetime | None) -> Path:
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


@pytest.fixture
def home_never_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = _home_with_heartbeat(tmp_path, heartbeat=None)
    monkeypatch.setenv("HOME", str(home))
    return home


def test_heartbeat_never_run(snap_compare, home_never_run: Path) -> None:
    assert snap_compare(str(APP_SCRIPT), terminal_size=(80, 8))


def test_heartbeat_fresh_frozen(snap_compare) -> None:
    """Wave-3 T-020. Harness freezes `_read_display` to a fixed
    HeartbeatDisplay so the SVG baseline doesn't drift with wall time."""
    assert snap_compare(str(APP_SCRIPT_FRESH_FROZEN), terminal_size=(80, 8))


def test_heartbeat_stale_frozen(snap_compare) -> None:
    """Wave-3 T-020. Stale variant — different marker, warning colour
    via the `-stale` CSS class."""
    assert snap_compare(str(APP_SCRIPT_STALE), terminal_size=(80, 8))
