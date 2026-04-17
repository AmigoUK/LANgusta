"""Monitor runner tests — single-cycle executor.

Verifies:
  - Due checks are executed; non-due are left alone.
  - CheckResult is persisted via record_result.
  - On state transition ok→fail, a 'monitor_event' timeline entry is written.
  - On fail→ok, a recovery entry is written.
  - Checks that remain in the same state don't re-spam the timeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.monitor.checks.base import CheckResult
from langusta.monitor.runner import run_once

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


class _StubCheck:
    """Test check — returns a pre-specified CheckResult."""

    def __init__(self, result: CheckResult) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def run(self, *, target: str, **config: object) -> CheckResult:
        self.calls.append((target, dict(config)))
        return self.result


def _seeded_with_check(tmp_path: Path) -> tuple[Path, int, int]:
    db = tmp_path / "mon.sqlite"
    migrate(db)
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="router", primary_ip="10.0.0.1", now=NOW,
        )
        cid = mon_dal.enable_check(
            conn, asset_id=aid, kind="icmp", interval_seconds=60, now=NOW,
        )
    return db, aid, cid


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_executes_due_check(tmp_path: Path) -> None:
    db, aid, _cid = _seeded_with_check(tmp_path)
    ok = _StubCheck(CheckResult(status="ok", latency_ms=1.5, detail=None))
    # list_due uses `now`; first run always due (never_run).
    run_at = NOW + timedelta(seconds=1)
    with connect(db) as conn:
        summary = await run_once(
            conn, now=run_at, check_registry={"icmp": ok, "tcp": ok, "http": ok},
        )
        results = mon_dal.list_results_for_asset(conn, asset_id=aid)
    assert summary.executed == 1
    assert len(results) == 1
    assert results[0].status == "ok"
    assert ok.calls == [("10.0.0.1", {})]


@pytest.mark.asyncio
async def test_run_once_skips_non_due(tmp_path: Path) -> None:
    db, aid, cid = _seeded_with_check(tmp_path)
    ok = _StubCheck(CheckResult(status="ok", latency_ms=1.0, detail=None))
    # Record a very-recent run; now+30s is < 60s interval -> not due.
    with connect(db) as conn:
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="ok",
            latency_ms=1.0, detail=None, now=NOW,
        )
        summary = await run_once(
            conn, now=NOW + timedelta(seconds=30),
            check_registry={"icmp": ok, "tcp": ok, "http": ok},
        )
    assert summary.executed == 0
    assert ok.calls == []


# ---------------------------------------------------------------------------
# Timeline state transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_fail_writes_monitor_event_timeline_entry(tmp_path: Path) -> None:
    db, aid, _ = _seeded_with_check(tmp_path)
    fail = _StubCheck(CheckResult(status="fail", latency_ms=None, detail="no response"))
    with connect(db) as conn:
        await run_once(
            conn, now=NOW,
            check_registry={"icmp": fail, "tcp": fail, "http": fail},
        )
        entries = tl_dal.list_by_asset(conn, aid)
    events = [e for e in entries if e.kind == "monitor_event"]
    assert len(events) == 1
    assert "fail" in events[0].body.lower()
    assert events[0].author == "monitor"


@pytest.mark.asyncio
async def test_ok_to_fail_transition_writes_entry(tmp_path: Path) -> None:
    db, aid, cid = _seeded_with_check(tmp_path)
    # Seed a prior OK directly on the check row.
    with connect(db) as conn:
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="ok",
            latency_ms=1.0, detail=None, now=NOW,
        )
    fail = _StubCheck(CheckResult(status="fail", latency_ms=None, detail="down"))
    with connect(db) as conn:
        await run_once(
            conn, now=NOW + timedelta(minutes=2),
            check_registry={"icmp": fail, "tcp": fail, "http": fail},
        )
        entries = tl_dal.list_by_asset(conn, aid)
    events = [e for e in entries if e.kind == "monitor_event"]
    assert len(events) == 1
    assert "fail" in events[0].body.lower()


@pytest.mark.asyncio
async def test_fail_to_ok_transition_writes_recovery_entry(tmp_path: Path) -> None:
    db, aid, cid = _seeded_with_check(tmp_path)
    with connect(db) as conn:
        mon_dal.record_result(
            conn, check_id=cid, asset_id=aid, status="fail",
            latency_ms=None, detail="down", now=NOW,
        )
    ok = _StubCheck(CheckResult(status="ok", latency_ms=2.0, detail=None))
    with connect(db) as conn:
        await run_once(
            conn, now=NOW + timedelta(minutes=2),
            check_registry={"icmp": ok, "tcp": ok, "http": ok},
        )
        entries = tl_dal.list_by_asset(conn, aid)
    events = [e for e in entries if e.kind == "monitor_event"]
    assert len(events) == 1
    assert "recover" in events[0].body.lower() or "ok" in events[0].body.lower()


@pytest.mark.asyncio
async def test_same_state_writes_no_duplicate_entry(tmp_path: Path) -> None:
    """Consecutive OKs (or consecutive fails) must not spam the timeline."""
    db, aid, _cid = _seeded_with_check(tmp_path)
    ok = _StubCheck(CheckResult(status="ok", latency_ms=1.0, detail=None))
    with connect(db) as conn:
        await run_once(
            conn, now=NOW,
            check_registry={"icmp": ok, "tcp": ok, "http": ok},
        )
        await run_once(
            conn, now=NOW + timedelta(minutes=2),
            check_registry={"icmp": ok, "tcp": ok, "http": ok},
        )
        entries = tl_dal.list_by_asset(conn, aid)
    # First-time transition from None->ok emits a recovery entry. Second
    # consecutive OK should emit nothing.
    events = [e for e in entries if e.kind == "monitor_event"]
    assert len(events) <= 1  # exactly 1 if we write a "came up" on first ok, else 0


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_updates_heartbeat(tmp_path: Path) -> None:
    db, _, _ = _seeded_with_check(tmp_path)
    ok = _StubCheck(CheckResult(status="ok", latency_ms=1.0, detail=None))
    with connect(db) as conn:
        await run_once(
            conn, now=NOW,
            check_registry={"icmp": ok, "tcp": ok, "http": ok},
        )
        hb = mon_dal.get_heartbeat(conn)
    assert hb == NOW
