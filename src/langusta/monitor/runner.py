"""Single-cycle monitor runner.

`run_once(conn, *, now)`:
  - Loads due `monitoring_checks`.
  - Executes each via the appropriate Check implementation, concurrently.
  - Persists `check_results` and updates `monitoring_checks.last_*`.
  - On state transitions (ok↔fail), writes a `monitor_event` timeline entry
    via the insert-only DAL — so monitoring events become first-class
    citizens of the asset's immutable history (spec §4 Pillar C).
  - Updates `meta.daemon_heartbeat`.

The function is called both by `langusta monitor run` (single-shot) and
by the long-running daemon loop (M7+).
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langusta.db import assets as assets_dal
from langusta.db import monitoring as mon_dal
from langusta.db import notifications as notif_dal
from langusta.db import timeline as tl_dal
from langusta.monitor.checks.base import Check, CheckResult
from langusta.monitor.checks.http import HttpCheck
from langusta.monitor.checks.icmp import IcmpCheck
from langusta.monitor.checks.tcp import TcpCheck
from langusta.monitor.notifications import MonitorEvent
from langusta.monitor.notifications import dispatch as dispatch_event

DEFAULT_REGISTRY: dict[str, Check] = {
    "icmp": IcmpCheck(),
    "tcp": TcpCheck(),
    "http": HttpCheck(),
}


@dataclass(frozen=True, slots=True)
class RunSummary:
    executed: int
    ok_count: int
    fail_count: int
    transitions: int


async def run_once(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    check_registry: dict[str, Check] | None = None,
    notifications_logfile: Path | None = None,
) -> RunSummary:
    registry = check_registry if check_registry is not None else DEFAULT_REGISTRY
    due = mon_dal.list_due(conn, now=now)

    # Load sinks up-front so in-flight inserts don't affect this cycle's
    # notification targets.
    sinks = notif_dal.list_all(conn, enabled_only=True)

    # Dispatch all due checks concurrently.
    tasks = []
    for check in due:
        impl = registry.get(check.kind)
        if impl is None:
            continue
        tasks.append(
            _run_one(
                check, impl, conn, now,
                sinks=sinks,
                notifications_logfile=notifications_logfile,
            )
        )
    outcomes = await asyncio.gather(*tasks) if tasks else []

    mon_dal.set_heartbeat(conn, now=now)

    executed = len(outcomes)
    ok_count = sum(1 for o in outcomes if o.status == "ok")
    fail_count = sum(1 for o in outcomes if o.status == "fail")
    transitions = sum(1 for o in outcomes if o.transitioned)

    return RunSummary(
        executed=executed, ok_count=ok_count, fail_count=fail_count,
        transitions=transitions,
    )


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: str
    transitioned: bool


async def _run_one(
    check: mon_dal.MonitoringCheck,
    impl: Check,
    conn: sqlite3.Connection,
    now: datetime,
    *,
    sinks: list,
    notifications_logfile: Path | None,
) -> _Outcome:
    asset = assets_dal.get_by_id(conn, check.asset_id)
    target = check.target or (asset.primary_ip if asset is not None else None)
    if target is None:
        # No target? record a fail without spamming the timeline.
        mon_dal.record_result(
            conn, check_id=check.id, asset_id=check.asset_id,
            status="fail", latency_ms=None, detail="no target configured",
            now=now,
        )
        return _Outcome(status="fail", transitioned=False)

    config: dict[str, object] = {}
    if check.port is not None:
        config["port"] = check.port
    if check.path is not None:
        config["path"] = check.path

    try:
        result: CheckResult = await impl.run(target=target, **config)
    except Exception as exc:
        result = CheckResult(status="fail", latency_ms=None, detail=str(exc))

    prior_status = check.last_status
    mon_dal.record_result(
        conn,
        check_id=check.id, asset_id=check.asset_id,
        status=result.status, latency_ms=result.latency_ms,
        detail=result.detail, now=now,
    )

    transitioned = False
    became_ok = False
    # First result for a check (prior_status=None) counts as a transition
    # only when it's a failure — we don't spam "came up" entries for every
    # newly-enabled check that happens to be reachable.
    if (prior_status is None and result.status == "fail") or (prior_status == "ok" and result.status == "fail"):
        transitioned = True
        _write_monitor_event(conn, check, result, now, became_ok=False)
    elif prior_status == "fail" and result.status == "ok":
        transitioned = True
        became_ok = True
        _write_monitor_event(conn, check, result, now, became_ok=True)

    if transitioned and notifications_logfile is not None:
        event = MonitorEvent(
            asset_id=check.asset_id,
            asset_hostname=asset.hostname if asset is not None else None,
            asset_ip=target,
            kind="recovery" if became_ok else "failure",
            check_kind=check.kind,
            detail=result.detail,
            occurred_at=now,
        )
        await dispatch_event(event, sinks=sinks, logfile_path=notifications_logfile)

    return _Outcome(status=result.status, transitioned=transitioned)


def _write_monitor_event(
    conn: sqlite3.Connection,
    check: mon_dal.MonitoringCheck,
    result: CheckResult,
    now: datetime,
    *,
    became_ok: bool,
) -> None:
    if became_ok:
        body = f"Monitor {check.kind} recovered (ok)"
    else:
        body = f"Monitor {check.kind} failed"
        if result.detail:
            body += f": {result.detail}"
    tl_dal.append_entry(
        conn,
        asset_id=check.asset_id,
        kind="monitor_event",
        body=body,
        now=now,
        author="monitor",
    )
