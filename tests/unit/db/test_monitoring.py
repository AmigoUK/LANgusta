"""Monitoring DAL tests.

Covers enable/disable/list/list_due/record_result + heartbeat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[Path, int]:
    db = tmp_path / "mon.sqlite"
    migrate(db)
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1", now=NOW,
        )
    return db, aid


# ---------------------------------------------------------------------------
# enable / disable / list
# ---------------------------------------------------------------------------


def test_enable_check_returns_id(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
    assert isinstance(cid, int) and cid > 0


def test_enable_check_default_enabled_true(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.enabled is True


def test_enable_rejects_sub_10s_interval(seeded) -> None:
    import sqlite3

    db, aid = seeded
    with connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=5, now=NOW,
        )


def test_http_check_accepts_port_and_path(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="http", interval_seconds=300,
            port=443, path="/healthz", now=NOW,
        )
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.port == 443
    assert check.path == "/healthz"


def test_disable_check_flips_enabled_false(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.disable_check(conn, cid)
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.enabled is False


def test_set_check_enabled_flips_disabled_back_on(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.disable_check(conn, cid)
        mon_dal.set_check_enabled(conn, cid, enabled=True)
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.enabled is True


def test_set_check_enabled_false_disables(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.set_check_enabled(conn, cid, enabled=False)
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.enabled is False


def test_list_checks_for_asset(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.enable_check(
            conn, asset_id=aid, kind="http", interval_seconds=300,
            port=443, now=NOW,
        )
        checks = mon_dal.list_checks(conn, asset_id=aid)
    assert {c.kind for c in checks} == {"icmp", "http"}


# ---------------------------------------------------------------------------
# list_due
# ---------------------------------------------------------------------------


def test_list_due_includes_never_run(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        due = mon_dal.list_due(conn, now=NOW)
    assert [c.id for c in due] == [cid]


def test_list_due_excludes_recently_run(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        # Mark as run "now".
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="ok",
            latency_ms=1.2, detail=None, now=NOW,
        )
        # Not yet due — 30 seconds later with a 60s interval.
        due = mon_dal.list_due(conn, now=NOW + timedelta(seconds=30))
    assert due == []


def test_list_due_includes_overdue(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="ok",
            latency_ms=1.2, detail=None, now=NOW,
        )
        due = mon_dal.list_due(conn, now=NOW + timedelta(seconds=120))
    assert [c.id for c in due] == [cid]


def test_list_due_excludes_disabled(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.disable_check(conn, cid)
        due = mon_dal.list_due(conn, now=NOW)
    assert due == []


# ---------------------------------------------------------------------------
# record_result
# ---------------------------------------------------------------------------


def test_record_result_stores_row(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="ok",
            latency_ms=2.3, detail=None, now=NOW,
        )
        results = mon_dal.list_results_for_asset(conn, asset_id=aid)
    assert len(results) == 1
    r = results[0]
    assert r.status == "ok"
    assert r.latency_ms == 2.3
    assert r.recorded_at == NOW


def test_record_result_updates_last_run_and_status(seeded) -> None:
    db, aid = seeded
    with connect(db) as conn:
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="fail",
            latency_ms=None, detail="no response", now=NOW,
        )
        check = mon_dal.get_by_id(conn, cid)
    assert check is not None
    assert check.last_status == "fail"
    assert check.last_run_at == NOW


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_write_read(seeded) -> None:
    db, _ = seeded
    with connect(db) as conn:
        assert mon_dal.get_heartbeat(conn) is None
        mon_dal.set_heartbeat(conn, now=NOW)
        hb = mon_dal.get_heartbeat(conn)
    assert hb == NOW


def test_heartbeat_stale_predicate(seeded) -> None:
    db, _ = seeded
    with connect(db) as conn:
        mon_dal.set_heartbeat(conn, now=NOW)
        hb = mon_dal.get_heartbeat(conn)
    assert hb is not None
    # Staleness check: 3 minutes later, with 2-minute tolerance → stale.
    stale = mon_dal.is_heartbeat_stale(
        hb, now=NOW + timedelta(minutes=3), tolerance_seconds=120,
    )
    fresh = mon_dal.is_heartbeat_stale(
        hb, now=NOW + timedelta(seconds=60), tolerance_seconds=120,
    )
    assert stale is True
    assert fresh is False
