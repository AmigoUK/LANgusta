"""Monitoring DAL.

Handles subscriptions (`monitoring_checks`) and historical results
(`check_results`), plus the daemon's liveness heartbeat stored under
`meta.daemon_heartbeat`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from langusta.db import meta as meta_dal

VALID_KINDS = frozenset({"icmp", "tcp", "http"})
_HEARTBEAT_KEY = "daemon_heartbeat"


@dataclass(frozen=True, slots=True)
class MonitoringCheck:
    id: int
    asset_id: int
    kind: str
    target: str | None
    port: int | None
    path: str | None
    interval_seconds: int
    enabled: bool
    created_at: datetime
    last_run_at: datetime | None
    last_status: str | None


@dataclass(frozen=True, slots=True)
class CheckResultRow:
    id: int
    check_id: int
    asset_id: int
    status: str
    latency_ms: float | None
    detail: str | None
    recorded_at: datetime


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw is not None else None


def _row_to_check(row: sqlite3.Row) -> MonitoringCheck:
    return MonitoringCheck(
        id=int(row["id"]),
        asset_id=int(row["asset_id"]),
        kind=row["kind"],
        target=row["target"],
        port=row["port"],
        path=row["path"],
        interval_seconds=int(row["interval_seconds"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_run_at=_parse_iso(row["last_run_at"]),
        last_status=row["last_status"],
    )


def _row_to_result(row: sqlite3.Row) -> CheckResultRow:
    return CheckResultRow(
        id=int(row["id"]),
        check_id=int(row["check_id"]),
        asset_id=int(row["asset_id"]),
        status=row["status"],
        latency_ms=row["latency_ms"],
        detail=row["detail"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
    )


# ---------------------------------------------------------------------------
# enable / disable / list
# ---------------------------------------------------------------------------


def enable_check(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    kind: str,
    interval_seconds: int,
    target: str | None = None,
    port: int | None = None,
    path: str | None = None,
    now: datetime,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown kind {kind!r}; valid: {sorted(VALID_KINDS)}")
    row = conn.execute(
        "INSERT INTO monitoring_checks ("
        "asset_id, kind, target, port, path, interval_seconds, enabled, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, 1, ?) RETURNING id",
        (asset_id, kind, target, port, path, interval_seconds, _iso(now)),
    ).fetchone()
    return int(row[0])


def disable_check(conn: sqlite3.Connection, check_id: int) -> None:
    conn.execute(
        "UPDATE monitoring_checks SET enabled = 0 WHERE id = ?", (check_id,),
    )


_CHECK_COLS = (
    "id, asset_id, kind, target, port, path, interval_seconds, enabled, "
    "created_at, last_run_at, last_status"
)


def get_by_id(conn: sqlite3.Connection, check_id: int) -> MonitoringCheck | None:
    row = conn.execute(
        f"SELECT {_CHECK_COLS} FROM monitoring_checks WHERE id = ?",
        (check_id,),
    ).fetchone()
    return _row_to_check(row) if row is not None else None


def list_checks(
    conn: sqlite3.Connection,
    *,
    asset_id: int | None = None,
    enabled_only: bool = False,
) -> list[MonitoringCheck]:
    clauses: list[str] = []
    params: list = []
    if asset_id is not None:
        clauses.append("asset_id = ?")
        params.append(asset_id)
    if enabled_only:
        clauses.append("enabled = 1")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT {_CHECK_COLS} FROM monitoring_checks{where} ORDER BY id",
        tuple(params),
    ).fetchall()
    return [_row_to_check(r) for r in rows]


def list_due(
    conn: sqlite3.Connection, *, now: datetime,
) -> list[MonitoringCheck]:
    """Return enabled checks that are due to run at `now`.

    A check is due if `last_run_at IS NULL` or `now - last_run_at >= interval_seconds`.
    """
    rows = conn.execute(
        f"SELECT {_CHECK_COLS} FROM monitoring_checks "
        "WHERE enabled = 1 ORDER BY id"
    ).fetchall()
    due: list[MonitoringCheck] = []
    for row in rows:
        check = _row_to_check(row)
        if check.last_run_at is None:
            due.append(check)
            continue
        elapsed = (now - check.last_run_at).total_seconds()
        if elapsed >= check.interval_seconds:
            due.append(check)
    return due


# ---------------------------------------------------------------------------
# record_result
# ---------------------------------------------------------------------------


def record_result(
    conn: sqlite3.Connection,
    *,
    check_id: int,
    asset_id: int,
    status: str,
    latency_ms: float | None,
    detail: str | None,
    now: datetime,
) -> int:
    if status not in {"ok", "fail"}:
        raise ValueError(f"status must be 'ok' or 'fail', got {status!r}")
    iso = _iso(now)
    row = conn.execute(
        "INSERT INTO check_results ("
        "check_id, asset_id, status, latency_ms, detail, recorded_at"
        ") VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        (check_id, asset_id, status, latency_ms, detail, iso),
    ).fetchone()
    conn.execute(
        "UPDATE monitoring_checks SET last_run_at = ?, last_status = ? WHERE id = ?",
        (iso, status, check_id),
    )
    return int(row[0])


def list_results_for_asset(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    limit: int = 50,
) -> list[CheckResultRow]:
    rows = conn.execute(
        "SELECT id, check_id, asset_id, status, latency_ms, detail, recorded_at "
        "FROM check_results WHERE asset_id = ? "
        "ORDER BY recorded_at DESC LIMIT ?",
        (asset_id, limit),
    ).fetchall()
    return [_row_to_result(r) for r in rows]


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def get_heartbeat(conn: sqlite3.Connection) -> datetime | None:
    raw = meta_dal.get(conn, _HEARTBEAT_KEY)
    return _parse_iso(raw)


def set_heartbeat(conn: sqlite3.Connection, *, now: datetime) -> None:
    meta_dal.set_value(conn, _HEARTBEAT_KEY, _iso(now), now=now)


def is_heartbeat_stale(
    heartbeat: datetime | None,
    *,
    now: datetime,
    tolerance_seconds: int,
) -> bool:
    if heartbeat is None:
        return True
    return (now - heartbeat) > timedelta(seconds=tolerance_seconds)
