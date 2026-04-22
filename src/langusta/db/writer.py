"""The single atomic write path for a scan observation.

Given an `Observation` + `scan_id`, this module resolves identity, merges
scan data into the asset via `core.provenance.merge_scan_result`, and
writes everything (asset upsert, field_provenance, MAC binding, timeline
entries, proposed_changes, review_queue) in a single transaction.

This is the place where the scanner-proposes-human-disposes invariant and
the immutable-timeline invariant meet the database. Every scan path —
orchestrator in M2, monitor events in M7 — routes through here.

Lives in `db/` because it composes raw SQL; `core/` stays stdlib-pure.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from langusta.core.identity import (
    Ambiguous,
    AssetIdentity,
    Candidate,
    Insert,
    Update,
    resolve,
)
from langusta.core.provenance import FieldProvenance, FieldValue, merge_scan_result
from langusta.db import proposed_changes as pc_dal
from langusta.db import timeline as tl_dal

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One scanned device — the unit of work for `apply_scan_observation`."""

    primary_ip: str
    hostname: str | None = None
    mac: str | None = None
    vendor: str | None = None
    detected_os: str | None = None
    device_type: str | None = None
    # open_ports is reported through the timeline (`scan_diff` entry body);
    # it is not a persisted column on `assets` in v1.
    open_ports: frozenset[int] = frozenset()


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


Outcome = Inserted | Updated | Deferred


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_SCANNABLE_FIELDS = (
    "hostname",
    "primary_ip",
    "vendor",
    "detected_os",
    "device_type",
)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _obs_to_fields(obs: Observation) -> dict[str, str]:
    """Return the non-None scannable fields as a flat dict."""
    out: dict[str, str] = {}
    for name in _SCANNABLE_FIELDS:
        value = getattr(obs, name)
        if value is not None:
            out[name] = value
    return out


def _obs_to_candidate(obs: Observation) -> Candidate:
    macs = frozenset({obs.mac.lower()}) if obs.mac else frozenset()
    return Candidate(hostname=obs.hostname, primary_ip=obs.primary_ip, macs=macs)


def _list_identities(conn: sqlite3.Connection) -> list[AssetIdentity]:
    """Project the assets + mac_addresses tables into `AssetIdentity` records."""
    asset_rows = conn.execute(
        "SELECT id, hostname, primary_ip FROM assets"
    ).fetchall()
    mac_rows = conn.execute(
        "SELECT asset_id, mac FROM mac_addresses"
    ).fetchall()
    macs_by_asset: dict[int, set[str]] = {}
    for r in mac_rows:
        macs_by_asset.setdefault(int(r["asset_id"]), set()).add(r["mac"])
    return [
        AssetIdentity(
            asset_id=int(r["id"]),
            hostname=r["hostname"],
            primary_ip=r["primary_ip"],
            macs=frozenset(macs_by_asset.get(int(r["id"]), set())),
        )
        for r in asset_rows
    ]


def _get_existing_field_values(
    conn: sqlite3.Connection, asset_id: int
) -> dict[str, FieldValue]:
    """Provenance map for an asset, with each FieldValue.value populated."""
    prov_rows = conn.execute(
        "SELECT field, provenance, set_at FROM field_provenance "
        "WHERE asset_id = ?",
        (asset_id,),
    ).fetchall()
    if not prov_rows:
        return {}
    asset_row = conn.execute(
        "SELECT hostname, primary_ip, vendor, detected_os, device_type "
        "FROM assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    if asset_row is None:
        return {}
    out: dict[str, FieldValue] = {}
    for r in prov_rows:
        name = str(r["field"])
        if name not in _SCANNABLE_FIELDS:
            continue
        value = asset_row[name]
        if value is None:
            continue
        out[name] = FieldValue(
            value=value,
            provenance=FieldProvenance(r["provenance"]),
            set_at=datetime.fromisoformat(r["set_at"]),
        )
    return out


def _bind_mac(
    conn: sqlite3.Connection, asset_id: int, mac: str, *, now: datetime
) -> bool:
    """Ensure (asset_id, mac) is present in mac_addresses. Returns True if the
    MAC was newly bound to this asset, False if it was already there.

    Silent if the MAC is bound to a DIFFERENT asset — the resolver is
    supposed to catch that case and return Ambiguous. If we reach here it's
    a DAL-level race we don't auto-fix.
    """
    now_iso = _iso(now)
    normalised = mac.lower()
    row = conn.execute(
        "SELECT asset_id FROM mac_addresses WHERE mac = ?",
        (normalised,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO mac_addresses (asset_id, mac, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (asset_id, normalised, now_iso, now_iso),
        )
        return True
    bound_asset_id = int(row["asset_id"])
    if bound_asset_id == asset_id:
        conn.execute(
            "UPDATE mac_addresses SET last_seen = ? WHERE mac = ?",
            (now_iso, normalised),
        )
        return False
    # MAC is bound to a different asset — the identity resolver is
    # supposed to catch this and return Ambiguous, but a concurrent
    # scan race can land here. Surface the conflict on both assets'
    # timelines so the operator sees it rather than losing the signal
    # on a silent return.
    _append_timeline(
        conn,
        asset_id=bound_asset_id,
        kind="system",
        body=(
            f"MAC {normalised} also observed on asset #{asset_id} — "
            "possible collision or identity race; resolver did not "
            "catch this"
        ),
        now=now,
        author="scanner",
    )
    _append_timeline(
        conn,
        asset_id=asset_id,
        kind="system",
        body=(
            f"MAC {normalised} is already bound to asset "
            f"#{bound_asset_id}; not re-bound"
        ),
        now=now,
        author="scanner",
    )
    return False


def _append_timeline(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    kind: str,
    body: str,
    now: datetime,
    author: str,
) -> None:
    tl_dal.append_entry(
        conn, asset_id=asset_id, kind=kind, body=body, now=now, author=author,
    )


# ---------------------------------------------------------------------------
# Insert / Update / Ambiguous dispatchers
# ---------------------------------------------------------------------------


def _apply_insert(
    conn: sqlite3.Connection,
    obs: Observation,
    *,
    now: datetime,
) -> Inserted:
    fields = _obs_to_fields(obs)
    now_iso = _iso(now)

    # Asset row with SCANNED source — every scannable field we saw gets a
    # provenance row.
    cur = conn.execute(
        "INSERT INTO assets (hostname, primary_ip, vendor, detected_os, "
        "device_type, first_seen, last_seen, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'scanned') RETURNING id",
        (
            fields.get("hostname"),
            fields.get("primary_ip"),
            fields.get("vendor"),
            fields.get("detected_os"),
            fields.get("device_type"),
            now_iso,
            now_iso,
        ),
    )
    asset_id = int(cur.fetchone()[0])

    for name in fields:
        conn.execute(
            "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
            "VALUES (?, ?, 'scanned', ?)",
            (asset_id, name, now_iso),
        )

    if obs.mac:
        _bind_mac(conn, asset_id, obs.mac, now=now)

    _append_timeline(
        conn,
        asset_id=asset_id,
        kind="system",
        body=f"Asset discovered at {obs.primary_ip}.",
        now=now,
        author="scanner",
    )
    if obs.open_ports:
        _append_timeline(
            conn,
            asset_id=asset_id,
            kind="scan_diff",
            body=f"Open ports: {', '.join(str(p) for p in sorted(obs.open_ports))}",
            now=now,
            author="scanner",
        )
    return Inserted(asset_id=asset_id)


def _apply_update(
    conn: sqlite3.Connection,
    obs: Observation,
    *,
    asset_id: int,
    scan_id: int,
    now: datetime,
) -> Updated:
    existing = _get_existing_field_values(conn, asset_id)
    incoming = _obs_to_fields(obs)
    applied, proposed = merge_scan_result(existing, incoming, now=now)

    now_iso = _iso(now)

    # Apply value changes. merge_scan_result already preserved MANUAL /
    # IMPORTED fields — everything in `applied` is safe to write.
    if applied:
        assignments = ", ".join(f"{name} = ?" for name in applied)
        values = [fv.value for fv in applied.values()]
        conn.execute(
            f"UPDATE assets SET {assignments}, last_seen = ? WHERE id = ?",
            (*values, now_iso, asset_id),
        )
        for name, fv in applied.items():
            conn.execute(
                "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(asset_id, field) DO UPDATE SET "
                "provenance = excluded.provenance, set_at = excluded.set_at",
                (asset_id, name, fv.provenance.value, _iso(fv.set_at)),
            )
    else:
        # No field changes; still refresh last_seen as a liveness signal.
        conn.execute(
            "UPDATE assets SET last_seen = ? WHERE id = ?",
            (now_iso, asset_id),
        )

    # Track whether the observation's MAC was genuinely new — so the scan_diff
    # entry lists it.
    new_mac_bound = False
    if obs.mac:
        new_mac_bound = _bind_mac(conn, asset_id, obs.mac, now=now)

    for change in proposed:
        pc_dal.insert(
            conn,
            asset_id=asset_id,
            field=change.field,
            current_value=change.current_value,
            current_provenance=change.current_provenance,
            proposed_value=change.proposed_value,
            observed_at=change.observed_at,
            scan_id=scan_id,
        )

    # `applied` also covers same-value timestamp refreshes (liveness); only
    # report fields whose VALUE changed as user-visible changes.
    visible_changes = tuple(
        name
        for name, fv in applied.items()
        if existing.get(name) is None or existing[name].value != fv.value
    )
    if visible_changes or new_mac_bound or obs.open_ports:
        parts: list[str] = []
        for name in visible_changes:
            parts.append(f"{name} -> {applied[name].value!r}")
        if new_mac_bound:
            parts.append(f"new MAC {obs.mac}")
        if obs.open_ports:
            ports_str = ", ".join(str(p) for p in sorted(obs.open_ports))
            parts.append(f"open ports: {ports_str}")
        _append_timeline(
            conn,
            asset_id=asset_id,
            kind="scan_diff",
            body="Scan observed: " + "; ".join(parts),
            now=now,
            author="scanner",
        )

    return Updated(
        asset_id=asset_id,
        applied_fields=visible_changes,
        proposed_changes=len(proposed),
    )


def _apply_ambiguous(
    conn: sqlite3.Connection,
    obs: Observation,
    resolution: Ambiguous,
    *,
    scan_id: int,
    now: datetime,
) -> Deferred:
    observation_json = json.dumps(
        {
            "primary_ip": obs.primary_ip,
            "hostname": obs.hostname,
            "mac": obs.mac,
            "vendor": obs.vendor,
            "detected_os": obs.detected_os,
            "device_type": obs.device_type,
        },
        separators=(",", ":"),
    )
    candidates_json = json.dumps(
        [{"asset_id": aid, "score": score} for aid, score in resolution.candidates],
        separators=(",", ":"),
    )
    row = conn.execute(
        "INSERT INTO review_queue (scan_id, observed_at, observation, candidates) "
        "VALUES (?, ?, ?, ?) RETURNING id",
        (scan_id, _iso(now), observation_json, candidates_json),
    ).fetchone()
    return Deferred(review_id=int(row[0]))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply_scan_observation(
    conn: sqlite3.Connection,
    obs: Observation,
    *,
    scan_id: int,
    now: datetime,
) -> Outcome:
    """Apply one scan observation, atomically.

    Resolves identity via `core.identity.resolve`, then dispatches to one of
    the three writers. The caller is responsible for transaction management
    (usually wrapping the enclosing scan in a single `connect()` block).
    """
    candidate = _obs_to_candidate(obs)
    identities = _list_identities(conn)
    resolution = resolve(candidate, identities)

    if isinstance(resolution, Insert):
        return _apply_insert(conn, obs, now=now)
    if isinstance(resolution, Update):
        return _apply_update(
            conn, obs, asset_id=resolution.asset_id, scan_id=scan_id, now=now
        )
    if isinstance(resolution, Ambiguous):
        return _apply_ambiguous(conn, obs, resolution, scan_id=scan_id, now=now)
    raise RuntimeError(f"unexpected resolution type: {type(resolution).__name__}")
