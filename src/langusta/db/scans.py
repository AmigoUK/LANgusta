"""Scans DAL — lifecycle of a scan record.

A scan is a row in the `scans` table. `start_scan` creates it with
`finished_at=NULL`; `finish_scan` fills in the completion fields. Timeline
entries and proposed_changes rows reference the scan_id so post-mortem
auditing can map back to "which scan caused this?".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime


class UnknownScanError(RuntimeError):
    """finish_scan called with a scan_id that doesn't exist."""


@dataclass(frozen=True, slots=True)
class Scan:
    id: int
    target: str
    started_at: datetime
    finished_at: datetime | None
    host_count: int | None
    note: str | None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    return datetime.fromisoformat(raw)


def _row_to_scan(row: sqlite3.Row) -> Scan:
    return Scan(
        id=int(row["id"]),
        target=row["target"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=_parse_iso(row["finished_at"]),
        host_count=row["host_count"],
        note=row["note"],
    )


def start_scan(
    conn: sqlite3.Connection, *, target: str, now: datetime, note: str | None = None
) -> int:
    """Create a new scan row. Returns the new id."""
    row = conn.execute(
        "INSERT INTO scans (target, started_at, note) VALUES (?, ?, ?) RETURNING id",
        (target, _iso(now), note),
    ).fetchone()
    return int(row[0])


def finish_scan(
    conn: sqlite3.Connection, scan_id: int, *, host_count: int, now: datetime
) -> None:
    cur = conn.execute(
        "UPDATE scans SET finished_at = ?, host_count = ? WHERE id = ?",
        (_iso(now), host_count, scan_id),
    )
    if cur.rowcount == 0:
        raise UnknownScanError(f"scan_id={scan_id} not found")


def get_by_id(conn: sqlite3.Connection, scan_id: int) -> Scan | None:
    row = conn.execute(
        "SELECT id, target, started_at, finished_at, host_count, note "
        "FROM scans WHERE id = ?",
        (scan_id,),
    ).fetchone()
    return _row_to_scan(row) if row is not None else None


def list_recent(conn: sqlite3.Connection, *, limit: int = 20) -> list[Scan]:
    rows = conn.execute(
        "SELECT id, target, started_at, finished_at, host_count, note "
        "FROM scans ORDER BY started_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_scan(r) for r in rows]
