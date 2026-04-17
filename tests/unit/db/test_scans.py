"""Tests for db/scans.py — scan record lifecycle.

A scan is represented as one row in the `scans` table. `start_scan` records
a new row with `finished_at=NULL`; `finish_scan` fills in the completion
fields. Identity ties proposed_changes and review_queue rows back to the
scan that produced them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.db import scans as scans_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=42)


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "scans.sqlite"
    migrate(db)
    return db


def test_start_scan_returns_scan_id(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        scan_id = scans_dal.start_scan(conn, target="192.168.1.0/24", now=NOW)
    assert isinstance(scan_id, int) and scan_id > 0


def test_start_scan_records_target_and_start_time(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        sid = scans_dal.start_scan(conn, target="10.0.0.0/30", now=NOW)
        scan = scans_dal.get_by_id(conn, sid)
    assert scan is not None
    assert scan.target == "10.0.0.0/30"
    assert scan.started_at == NOW
    assert scan.finished_at is None
    assert scan.host_count is None


def test_finish_scan_fills_completion_fields(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        sid = scans_dal.start_scan(conn, target="10.0.0.0/30", now=NOW)
        scans_dal.finish_scan(conn, sid, host_count=3, now=LATER)
        scan = scans_dal.get_by_id(conn, sid)
    assert scan is not None
    assert scan.finished_at == LATER
    assert scan.host_count == 3


def test_finish_scan_on_unknown_id_raises(fresh_db: Path) -> None:
    with connect(fresh_db) as conn, pytest.raises(scans_dal.UnknownScanError):
        scans_dal.finish_scan(conn, 999, host_count=0, now=LATER)


def test_list_recent_returns_scans_newest_first(fresh_db: Path) -> None:
    with connect(fresh_db) as conn:
        a = scans_dal.start_scan(conn, target="1.2.3.0/24", now=NOW)
        b = scans_dal.start_scan(conn, target="1.2.4.0/24", now=LATER)
        recent = scans_dal.list_recent(conn, limit=10)
    assert [s.id for s in recent] == [b, a]
