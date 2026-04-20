"""Shared helpers for importer modules (Lansweeper, NetBox, ...).

All importers obey the same review-queue semantics as scans:
  * no match              → insert with provenance 'imported'
  * MAC match             → merge via `core.provenance.merge_scan_result`;
                            conflicts on protected fields become
                            `proposed_changes`, the rest apply with IMPORTED
                            provenance (escalating SCANNED fields).
  * IP-only match         → `review_queue` row (human picks)
  * MAC/IP point to
    different assets      → `review_queue` with both candidates

Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar A.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from langusta.core.provenance import merge_scan_result
from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import timeline as tl_dal

# Asset columns the importer may set. Subset of assets_dal._PROVENANCE_FIELDS;
# `criticality` is deliberately excluded (no external-tool equivalent) and
# identity fields like `mac` live on the MAC table, not here.
IMPORTABLE_FIELDS: tuple[str, ...] = (
    "hostname",
    "primary_ip",
    "vendor",
    "detected_os",
    "device_type",
    "description",
    "location",
    "owner",
    "management_url",
)


# ---------------------------------------------------------------------------
# Outcome + error types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Inserted:
    asset_id: int


@dataclass(frozen=True, slots=True)
class Updated:
    asset_id: int
    applied_fields: tuple[str, ...]
    proposed_changes: int


@dataclass(frozen=True, slots=True)
class Deferred:
    review_id: int


ImportOutcome = Inserted | Updated | Deferred


@dataclass(frozen=True, slots=True)
class RowError:
    line_number: int
    reason: str
    raw: dict[str, str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _resolve_identity(
    conn: sqlite3.Connection, *, mac: str | None, primary_ip: str | None
) -> tuple[int | None, int | None]:
    """Return (mac_asset_id, ip_asset_id) — either may be None."""
    mac_asset: int | None = None
    ip_asset: int | None = None
    if mac is not None:
        row = conn.execute(
            "SELECT asset_id FROM mac_addresses WHERE mac = ?", (mac.lower(),),
        ).fetchone()
        if row is not None:
            mac_asset = int(row["asset_id"])
    if primary_ip is not None:
        row = conn.execute(
            "SELECT id FROM assets WHERE primary_ip = ? ORDER BY id LIMIT 1",
            (primary_ip,),
        ).fetchone()
        if row is not None:
            ip_asset = int(row["id"])
    return mac_asset, ip_asset


# ---------------------------------------------------------------------------
# Insert path
# ---------------------------------------------------------------------------


def insert_imported_asset(
    conn: sqlite3.Connection,
    *,
    fields: dict[str, str],
    mac: str | None,
    now: datetime,
) -> int:
    """Insert a new asset row with source='imported'. Records per-field
    IMPORTED provenance for every provided field. Binds `mac` if given.
    Also appends a `kind='import'` timeline entry.
    """
    now_iso = _iso(now)

    columns = [f for f in IMPORTABLE_FIELDS if fields.get(f) is not None]

    if columns:
        col_names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        values = [fields[f] for f in columns]
        cur = conn.execute(
            f"INSERT INTO assets ({col_names}, first_seen, last_seen, source) "
            f"VALUES ({placeholders}, ?, ?, 'imported') RETURNING id",
            (*values, now_iso, now_iso),
        )
    else:
        cur = conn.execute(
            "INSERT INTO assets (first_seen, last_seen, source) "
            "VALUES (?, ?, 'imported') RETURNING id",
            (now_iso, now_iso),
        )
    asset_id = int(cur.fetchone()[0])

    for name in columns:
        conn.execute(
            "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
            "VALUES (?, ?, 'imported', ?)",
            (asset_id, name, now_iso),
        )

    if mac:
        conn.execute(
            "INSERT INTO mac_addresses (asset_id, mac, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (asset_id, mac.lower(), now_iso, now_iso),
        )

    tl_dal.append_entry(
        conn,
        asset_id=asset_id,
        kind="import",
        body=_describe(fields, mac, prefix="Imported asset"),
        now=now,
        author="importer",
    )
    return asset_id


# ---------------------------------------------------------------------------
# Update path (MAC match)
# ---------------------------------------------------------------------------


def _apply_update(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    fields: dict[str, str],
    mac: str,
    now: datetime,
) -> Updated:
    existing = assets_dal.get_provenance(conn, asset_id)
    # merge_scan_result expects `incoming: dict[str, str]`; we filter to the
    # importable set (it already respects per-field presence).
    incoming = {k: v for k, v in fields.items() if k in IMPORTABLE_FIELDS}
    applied, proposed = merge_scan_result(existing, incoming, now=now)

    now_iso = _iso(now)

    if applied:
        assignments = ", ".join(f"{name} = ?" for name in applied)
        values = [fv.value for fv in applied.values()]
        conn.execute(
            f"UPDATE assets SET {assignments}, last_seen = ? WHERE id = ?",
            (*values, now_iso, asset_id),
        )
        # Escalate provenance to IMPORTED for every applied field — even those
        # `merge_scan_result` tagged SCANNED. Import is a human-curated source
        # and deserves protected-field status going forward.
        for name in applied:
            conn.execute(
                "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
                "VALUES (?, ?, 'imported', ?) "
                "ON CONFLICT(asset_id, field) DO UPDATE SET "
                "provenance = 'imported', set_at = excluded.set_at",
                (asset_id, name, now_iso),
            )
    else:
        conn.execute(
            "UPDATE assets SET last_seen = ? WHERE id = ?",
            (now_iso, asset_id),
        )

    # Refresh the matched MAC's last_seen for liveness reporting.
    conn.execute(
        "UPDATE mac_addresses SET last_seen = ? WHERE mac = ? AND asset_id = ?",
        (now_iso, mac.lower(), asset_id),
    )

    for change in proposed:
        pc_dal.insert(
            conn,
            asset_id=asset_id,
            field=change.field,
            current_value=change.current_value,
            current_provenance=change.current_provenance,
            proposed_value=change.proposed_value,
            observed_at=change.observed_at,
            scan_id=None,
        )

    visible_changes = tuple(
        name
        for name, fv in applied.items()
        if existing.get(name) is None or existing[name].value != fv.value
    )

    if visible_changes or proposed:
        parts: list[str] = [
            f"{name} -> {applied[name].value!r}" for name in visible_changes
        ]
        if proposed:
            parts.append(f"{len(proposed)} proposed change(s)")
        tl_dal.append_entry(
            conn,
            asset_id=asset_id,
            kind="import",
            body="Import merged: " + "; ".join(parts),
            now=now,
            author="importer",
        )

    return Updated(
        asset_id=asset_id,
        applied_fields=visible_changes,
        proposed_changes=len(proposed),
    )


# ---------------------------------------------------------------------------
# Review-queue path
# ---------------------------------------------------------------------------


def _defer_to_review(
    conn: sqlite3.Connection,
    *,
    fields: dict[str, str],
    mac: str | None,
    candidates: list[dict[str, object]],
    now: datetime,
) -> Deferred:
    observation: dict[str, str] = dict(fields)
    if mac is not None:
        observation["mac"] = mac.lower()
    observation_json = json.dumps(observation, separators=(",", ":"), sort_keys=True)
    candidates_json = json.dumps(candidates, separators=(",", ":"))
    row = conn.execute(
        "INSERT INTO review_queue (scan_id, observed_at, observation, candidates) "
        "VALUES (NULL, ?, ?, ?) RETURNING id",
        (_iso(now), observation_json, candidates_json),
    ).fetchone()
    return Deferred(review_id=int(row[0]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply_imported_observation(
    conn: sqlite3.Connection,
    *,
    fields: dict[str, str],
    mac: str | None,
    now: datetime,
) -> ImportOutcome:
    """Route one imported row through identity resolution + provenance rules.

    `fields` is a flat dict of `IMPORTABLE_FIELDS` subset → string value.
    Extra keys are ignored. The caller is responsible for IP validation and
    header normalisation.
    """
    primary_ip = fields.get("primary_ip")
    mac_asset, ip_asset = _resolve_identity(conn, mac=mac, primary_ip=primary_ip)

    if mac_asset is None and ip_asset is None:
        asset_id = insert_imported_asset(conn, fields=fields, mac=mac, now=now)
        return Inserted(asset_id=asset_id)

    if mac_asset is not None and (ip_asset is None or ip_asset == mac_asset):
        # MAC points at this asset (IP either unknown or agrees) → merge.
        assert mac is not None  # narrowing for type checkers
        return _apply_update(
            conn, asset_id=mac_asset, fields=fields, mac=mac, now=now,
        )

    if mac_asset is None and ip_asset is not None:
        return _defer_to_review(
            conn,
            fields=fields,
            mac=mac,
            candidates=[{"asset_id": ip_asset, "score": 80, "reason": "ip_match"}],
            now=now,
        )

    # MAC and IP point to DIFFERENT existing assets — ambiguous.
    return _defer_to_review(
        conn,
        fields=fields,
        mac=mac,
        candidates=[
            {"asset_id": mac_asset, "score": 90, "reason": "mac_match"},
            {"asset_id": ip_asset, "score": 80, "reason": "ip_match"},
        ],
        now=now,
    )


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


def _describe(fields: dict[str, str], mac: str | None, *, prefix: str) -> str:
    bits: list[str] = []
    for key in sorted(fields):
        bits.append(f"{key}={fields[key]!r}")
    if mac:
        bits.append(f"mac={mac.lower()!r}")
    return f"{prefix} ({', '.join(bits)})"
