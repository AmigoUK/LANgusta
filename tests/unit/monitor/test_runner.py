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


# ---------------------------------------------------------------------------
# snmp_oid dispatch + credential caching
# ---------------------------------------------------------------------------


class _RecordingSnmpCheck:
    """Captures the config dict every time it's invoked."""

    def __init__(self, result: CheckResult) -> None:
        self.result = result
        self.configs: list[dict] = []

    async def run(self, *, target: str, **config: object) -> CheckResult:
        self.configs.append({"target": target, **config})
        return self.result


def _seed_snmp_oid_setup(tmp_path: Path):
    """Seed DB with two snmp_oid checks sharing one credential (for cache test).

    Returns (db_path, check1_id, check2_id, credential_id, unlocked_vault).
    """
    from langusta.crypto import master_password as mp
    from langusta.db import credentials as cred_dal

    db = tmp_path / "mon.sqlite"
    migrate(db)

    with connect(db) as conn:
        mp.setup(conn, password="pw-long-enough-for-tests", now=NOW)
        vault = mp.unlock(conn, password="pw-long-enough-for-tests")

        aid1 = assets_dal.insert_manual(
            conn, hostname="sw1", primary_ip="10.0.0.1", now=NOW,
        )
        aid2 = assets_dal.insert_manual(
            conn, hostname="sw2", primary_ip="10.0.0.2", now=NOW,
        )

        cred_id = cred_dal.create(
            conn, label="snmp-shared", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
        c1 = mon_dal.enable_check(
            conn, asset_id=aid1, kind="snmp_oid", interval_seconds=60,
            oid="1.3.6.1.2.1.1.3.0", credential_id=cred_id, now=NOW,
        )
        c2 = mon_dal.enable_check(
            conn, asset_id=aid2, kind="snmp_oid", interval_seconds=60,
            oid="1.3.6.1.2.1.1.3.0", credential_id=cred_id, now=NOW,
        )
    return db, c1, c2, cred_id, vault


@pytest.mark.asyncio
async def test_snmp_oid_runner_passes_resolved_auth_to_check(tmp_path: Path) -> None:
    db, _c1, _c2, _cred_id, vault = _seed_snmp_oid_setup(tmp_path)
    recording = _RecordingSnmpCheck(
        CheckResult(status="ok", latency_ms=2.0, detail="x"),
    )
    with connect(db) as conn:
        await run_once(
            conn, now=NOW, vault=vault,
            check_registry={"snmp_oid": recording},
        )
    assert len(recording.configs) == 2  # one call per check
    from langusta.scan.snmp.auth import SnmpV2cAuth
    for c in recording.configs:
        assert isinstance(c["snmp_auth"], SnmpV2cAuth)
        assert c["snmp_auth"].community == "public"
        assert c["oid"] == "1.3.6.1.2.1.1.3.0"


@pytest.mark.asyncio
async def test_snmp_oid_runner_caches_credential_decrypt(tmp_path: Path) -> None:
    """N checks sharing one credential_id => one decrypt, not N."""
    db, _c1, _c2, _cred_id, vault = _seed_snmp_oid_setup(tmp_path)

    from langusta.db import credentials as cred_dal
    calls: list[int] = []
    original = cred_dal.get_secret

    def counting_get_secret(conn, *, credential_id, vault):
        calls.append(credential_id)
        return original(conn, credential_id=credential_id, vault=vault)

    recording = _RecordingSnmpCheck(
        CheckResult(status="ok", latency_ms=2.0, detail="x"),
    )
    import langusta.monitor.runner as runner_mod
    runner_mod.cred_dal.get_secret = counting_get_secret  # type: ignore[attr-defined]
    try:
        with connect(db) as conn:
            await run_once(
                conn, now=NOW, vault=vault,
                check_registry={"snmp_oid": recording},
            )
    finally:
        runner_mod.cred_dal.get_secret = original  # type: ignore[attr-defined]
    assert calls == [1], f"expected exactly one decrypt, got {len(calls)}: {calls}"


@pytest.mark.asyncio
async def test_snmp_oid_without_vault_records_fail(tmp_path: Path) -> None:
    db, c1, _c2, _cred_id, _vault = _seed_snmp_oid_setup(tmp_path)
    recording = _RecordingSnmpCheck(
        CheckResult(status="ok", latency_ms=2.0, detail="x"),
    )
    with connect(db) as conn:
        await run_once(
            conn, now=NOW, vault=None,
            check_registry={"snmp_oid": recording},
        )
        # Check's run() was never called — blocked at config resolution.
        row = conn.execute(
            "SELECT status, detail FROM check_results WHERE check_id = ?",
            (c1,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "fail"
    assert "vault" in (row["detail"] or "").lower()
    assert len(recording.configs) == 0


# ---------------------------------------------------------------------------
# Wave-3 TEST-C-004 — timeout_seconds propagates to tcp/http check config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["tcp", "http"])
def test_resolve_config_passes_timeout_to_tcp_and_http(kind: str) -> None:
    """`MonitoringCheck.timeout_seconds` is persisted per-check but
    `_resolve_config` previously returned it only for snmp_oid and
    ssh_command. tcp and http dropped the value on the floor, leaving
    those kinds stuck on the Http/Tcp backends' hard-coded 5s default.
    Wave-3 finding C-004 (single-lens correctness, medium)."""
    from langusta.db.monitoring import MonitoringCheck
    from langusta.monitor.runner import _resolve_config

    check = MonitoringCheck(
        id=1, asset_id=1, kind=kind,
        target=None, port=80, path="/" if kind == "http" else None,
        interval_seconds=60, enabled=True,
        created_at=NOW, last_run_at=None, last_status=None,
        timeout_seconds=0.25,
    )
    cfg = _resolve_config(
        check,
        conn=None,  # type: ignore[arg-type]
        vault=None,
        snmp_client=None,
        ssh_client=None,
        cred_cache={},
    )
    assert cfg.get("timeout") == 0.25, (
        f"{kind}: timeout_seconds={check.timeout_seconds} dropped by "
        f"_resolve_config; resolved config={cfg}"
    )
