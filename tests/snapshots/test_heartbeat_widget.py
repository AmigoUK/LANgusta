"""Snapshot test for HeartbeatBar — only the time-stable `never_run` state.

Fresh/stale states carry a live "Ns ago" string that shifts between test
runs and would make snapshots flaky. Those code paths are covered by
`tests/unit/tui/test_heartbeat_format.py` against the pure formatter.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

APP_SCRIPT = Path(__file__).parent / "_heartbeat_fresh_app.py"


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
