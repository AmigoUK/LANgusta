"""Assets DAL — insert / list / read for the `assets` and `mac_addresses`
tables plus field-level provenance.

ADR-0001: raw SQL + thin DAL, one module per aggregate. Every call site
outside `db/` goes through these functions — no raw SQL elsewhere.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from langusta.core.models import Asset, normalize_mac
from langusta.core.provenance import FieldProvenance, FieldValue

# Asset columns that can carry field-level provenance.
_PROVENANCE_FIELDS = (
    "hostname",
    "primary_ip",
    "vendor",
    "detected_os",
    "device_type",
    "description",
    "location",
    "owner",
    "management_url",
    "criticality",
)


class DuplicateMacError(RuntimeError):
    """A MAC was submitted that's already bound to another asset."""


# ---------------------------------------------------------------------------
# Timestamp helpers (SQLite stores ISO-8601 strings; we parse/format here)
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


# ---------------------------------------------------------------------------
# insert_manual
# ---------------------------------------------------------------------------


def insert_manual(
    conn: sqlite3.Connection,
    *,
    hostname: str | None = None,
    primary_ip: str | None = None,
    mac: str | None = None,
    vendor: str | None = None,
    detected_os: str | None = None,
    device_type: str | None = None,
    description: str | None = None,
    location: str | None = None,
    owner: str | None = None,
    management_url: str | None = None,
    criticality: str | None = None,
    now: datetime,
) -> int:
    """Insert a new asset with `source='manual'` and per-field `manual`
    provenance for every caller-provided field. Returns the new asset_id.

    A single MAC may be supplied; M1 does not expose multi-MAC insert from
    the CLI. Additional MACs can be added via `_insert_mac` for tests or
    later milestones.
    """
    provided = {
        "hostname": hostname,
        "primary_ip": primary_ip,
        "vendor": vendor,
        "detected_os": detected_os,
        "device_type": device_type,
        "description": description,
        "location": location,
        "owner": owner,
        "management_url": management_url,
        "criticality": criticality,
    }

    now_iso = _iso(now)

    cur = conn.execute(
        "INSERT INTO assets ("
        "hostname, primary_ip, vendor, detected_os, device_type, "
        "description, location, owner, management_url, criticality, "
        "first_seen, last_seen, source"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual') "
        "RETURNING id",
        (
            hostname, primary_ip, vendor, detected_os, device_type,
            description, location, owner, management_url, criticality,
            now_iso, now_iso,
        ),
    )
    row = cur.fetchone()
    asset_id = int(row[0])

    for field_name, value in provided.items():
        if value is not None:
            conn.execute(
                "INSERT INTO field_provenance (asset_id, field, provenance, set_at) "
                "VALUES (?, ?, 'manual', ?)",
                (asset_id, field_name, now_iso),
            )

    if mac is not None:
        _insert_mac(conn, asset_id, mac, now=now)

    return asset_id


def _insert_mac(
    conn: sqlite3.Connection,
    asset_id: int,
    mac: str,
    *,
    now: datetime,
) -> None:
    """Bind a MAC to an asset. Normalises to lowercase. Raises
    DuplicateMacError if the MAC is already bound elsewhere."""
    normalised = normalize_mac(mac)
    now_iso = _iso(now)
    try:
        conn.execute(
            "INSERT INTO mac_addresses (asset_id, mac, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (asset_id, normalised, now_iso, now_iso),
        )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: mac_addresses.mac" in str(exc):
            raise DuplicateMacError(
                f"MAC {normalised} is already bound to another asset"
            ) from exc
        raise


# ---------------------------------------------------------------------------
# list_all + get_by_id
# ---------------------------------------------------------------------------


def _row_to_asset(row: sqlite3.Row, macs: list[str]) -> Asset:
    return Asset(
        id=int(row["id"]),
        hostname=row["hostname"],
        primary_ip=row["primary_ip"],
        vendor=row["vendor"],
        detected_os=row["detected_os"],
        device_type=row["device_type"],
        description=row["description"],
        location=row["location"],
        owner=row["owner"],
        management_url=row["management_url"],
        criticality=row["criticality"],
        first_seen=_parse_iso(row["first_seen"]),
        last_seen=_parse_iso(row["last_seen"]),
        source=row["source"],
        macs=macs,
    )


def list_all(conn: sqlite3.Connection) -> list[Asset]:
    """Return every asset, ordered by id ascending."""
    asset_rows = conn.execute(
        "SELECT id, hostname, primary_ip, vendor, detected_os, device_type, "
        "description, location, owner, management_url, criticality, "
        "first_seen, last_seen, source "
        "FROM assets ORDER BY id"
    ).fetchall()
    if not asset_rows:
        return []
    mac_rows = conn.execute(
        "SELECT asset_id, mac FROM mac_addresses ORDER BY asset_id, mac"
    ).fetchall()
    by_asset: dict[int, list[str]] = {}
    for r in mac_rows:
        by_asset.setdefault(int(r["asset_id"]), []).append(r["mac"])
    return [_row_to_asset(r, by_asset.get(int(r["id"]), [])) for r in asset_rows]


def get_by_id(conn: sqlite3.Connection, asset_id: int) -> Asset | None:
    row = conn.execute(
        "SELECT id, hostname, primary_ip, vendor, detected_os, device_type, "
        "description, location, owner, management_url, criticality, "
        "first_seen, last_seen, source "
        "FROM assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    if row is None:
        return None
    mac_rows = conn.execute(
        "SELECT mac FROM mac_addresses WHERE asset_id = ? ORDER BY mac",
        (asset_id,),
    ).fetchall()
    return _row_to_asset(row, [r["mac"] for r in mac_rows])


# ---------------------------------------------------------------------------
# Field-level provenance
# ---------------------------------------------------------------------------


def get_provenance(
    conn: sqlite3.Connection, asset_id: int
) -> dict[str, FieldValue]:
    """Return {field_name: FieldValue(...)} for an asset's recorded provenance.

    FieldValue.value is populated from the asset row for convenience; the
    authoritative `provenance` and `set_at` come from field_provenance.
    """
    prov_rows = conn.execute(
        "SELECT field, provenance, set_at FROM field_provenance "
        "WHERE asset_id = ?",
        (asset_id,),
    ).fetchall()
    if not prov_rows:
        return {}
    asset_row = conn.execute(
        "SELECT "
        + ", ".join(_PROVENANCE_FIELDS)
        + " FROM assets WHERE id = ?",
        (asset_id,),
    ).fetchone()
    if asset_row is None:
        return {}
    out: dict[str, FieldValue] = {}
    for r in prov_rows:
        field_name = str(r["field"])
        value = asset_row[field_name] if field_name in _PROVENANCE_FIELDS else None
        if value is None:
            # Field value was cleared after provenance was recorded; skip.
            continue
        out[field_name] = FieldValue(
            value=value,
            provenance=FieldProvenance(r["provenance"]),
            set_at=_parse_iso(r["set_at"]),
        )
    return out
