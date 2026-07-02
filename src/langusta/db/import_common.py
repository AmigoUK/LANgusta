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

from langusta.core.identity import (
    Ambiguous,
    Candidate,
    Insert,
    Resolution,
    Update,
    resolve,
)
from langusta.core.models import normalize_mac
from langusta.core.provenance import merge_scan_result
from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import timeline as tl_dal
from langusta.db.writer import list_identities

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


def _resolve_via_core(
    conn: sqlite3.Connection,
    *,
    hostname: str | None,
    primary_ip: str | None,
    mac: str | None,
) -> Resolution:
    """Delegate to ``core.identity.resolve`` for hostname-aware matching.

    This replaces the old MAC+IP-only resolver. ``core.identity.resolve``
    also checks hostname conflicts and returns ``Ambiguous`` when MAC points
    at asset A but hostname points at asset B (the Lansweeper-failure mode).
    """
    identities = list_identities(conn)
    macs = frozenset({normalize_mac(mac)}) if mac else frozenset()
    candidate = Candidate(hostname=hostname, primary_ip=primary_ip, macs=macs)
    return resolve(candidate, identities)


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
            (asset_id, normalize_mac(mac), now_iso, now_iso),
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
    mac: str | None,
    now: datetime,
) -> Updated:
    existing = assets_dal.get_provenance(conn, asset_id)
    # merge_scan_result expects `incoming: dict[str, str]`; we filter to the
    # importable set (it already respects per-field presence).
    incoming = {k: v for k, v in fields.items() if k in IMPORTABLE_FIELDS and v is not None}
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
    if mac is not None:
        conn.execute(
            "UPDATE mac_addresses SET last_seen = ? WHERE mac = ? AND asset_id = ?",
            (now_iso, normalize_mac(mac), asset_id),
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
        observation["mac"] = normalize_mac(mac)
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

    Uses ``core.identity.resolve`` for hostname-aware matching, so a row
    whose MAC points at asset A but hostname at asset B is deferred to the
    review queue (the Lansweeper-failure case).
    """
    resolution = _resolve_via_core(
        conn,
        hostname=fields.get("hostname"),
        primary_ip=fields.get("primary_ip"),
        mac=mac,
    )

    if isinstance(resolution, Insert):
        asset_id = insert_imported_asset(conn, fields=fields, mac=mac, now=now)
        return Inserted(asset_id=asset_id)

    if isinstance(resolution, Update):
        return _apply_update(
            conn, asset_id=resolution.asset_id, fields=fields, mac=mac, now=now,
        )

    # Ambiguous — defer to review queue.
    assert isinstance(resolution, Ambiguous)
    return _defer_to_review(
        conn,
        fields=fields,
        mac=mac,
        candidates=[
            {"asset_id": aid, "score": conf, "reason": resolution.reason}
            for aid, conf in resolution.candidates
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
        bits.append(f"mac={normalize_mac(mac)!r}")
    return f"{prefix} ({', '.join(bits)})"
