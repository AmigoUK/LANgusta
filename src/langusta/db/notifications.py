"""Notification-sinks DAL.

Each row configures a webhook / SMTP / extra logfile sink. The always-on
`~/.langusta/notifications.log` is handled by the dispatcher, not stored
here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

VALID_KINDS = frozenset({"webhook", "smtp", "logfile"})


class DuplicateLabel(ValueError):  # noqa: N818 — domain error
    """Two sinks with the same label isn't allowed."""


@dataclass(frozen=True, slots=True)
class NotificationSink:
    id: int
    label: str
    kind: str
    config: dict
    enabled: bool
    created_at: datetime


def _row(row: sqlite3.Row) -> NotificationSink:
    return NotificationSink(
        id=int(row["id"]),
        label=row["label"],
        kind=row["kind"],
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


_COLS = "id, label, kind, config, enabled, created_at"


def create(
    conn: sqlite3.Connection,
    *,
    label: str,
    kind: str,
    config: dict,
    now: datetime,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown sink kind {kind!r}; valid: {sorted(VALID_KINDS)}")
    try:
        row = conn.execute(
            "INSERT INTO notification_sinks (label, kind, config, enabled, created_at) "
            "VALUES (?, ?, ?, 1, ?) RETURNING id",
            (label, kind, json.dumps(config), _iso(now)),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            raise DuplicateLabel(f"sink label {label!r} already exists") from exc
        raise
    return int(row[0])


def get_by_label(conn: sqlite3.Connection, label: str) -> NotificationSink | None:
    row = conn.execute(
        f"SELECT {_COLS} FROM notification_sinks WHERE label = ?", (label,),
    ).fetchone()
    return _row(row) if row is not None else None


def list_all(
    conn: sqlite3.Connection, *, enabled_only: bool = False,
) -> list[NotificationSink]:
    where = " WHERE enabled = 1" if enabled_only else ""
    rows = conn.execute(
        f"SELECT {_COLS} FROM notification_sinks{where} ORDER BY id",
    ).fetchall()
    return [_row(r) for r in rows]


def disable(conn: sqlite3.Connection, sink_id: int) -> None:
    conn.execute(
        "UPDATE notification_sinks SET enabled = 0 WHERE id = ?", (sink_id,),
    )


def delete(conn: sqlite3.Connection, sink_id: int) -> None:
    conn.execute("DELETE FROM notification_sinks WHERE id = ?", (sink_id,))
