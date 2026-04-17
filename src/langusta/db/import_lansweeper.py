"""Lansweeper CSV importer.

Spec §7: the migration on-ramp for users leaving Lansweeper or NetBox.
Imported fields land with provenance='imported' so later scans don't
silently overwrite them — the same protection as manually-entered fields.

Deliberately conservative matching: if an imported row would collide with
an existing MAC or primary_ip we skip (count it in `skipped`) rather than
merge. For v1 the happy path is importing into an empty DB; advanced
merging arrives when we wire the importer through the review queue.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImportReport:
    imported: int
    skipped: int


# Field-mapping: Lansweeper column -> LANgusta `insert_manual` kwarg.
# We match case-insensitively and accept either Lansweeper's historical
# "AssetName"/"IPAddress" or the newer "Name"/"IP" variants. Order matters:
# earlier names in the tuple win when a single LANgusta field has multiple
# potential sources.
_MAPPING: dict[str, tuple[str, ...]] = {
    "hostname": ("assetname", "name"),
    "primary_ip": ("ipaddress", "ip"),
    "mac": ("mac", "macaddress"),
    "description": ("description",),
    "vendor": ("manufacturer", "vendor"),
    "device_type": ("type", "model"),
}


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    """Return the first non-empty value from the normalised row."""
    for name in candidates:
        value = row.get(name)
        if value:
            return value.strip() or None
    return None


def _normalise_headers(row: dict[str, str]) -> dict[str, str]:
    """Lowercase keys so the import is tolerant of ASSETNAME / AssetName."""
    return {str(k).strip().lower(): (v if v is not None else "") for k, v in row.items()}


def _mac_exists(conn: sqlite3.Connection, mac: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM mac_addresses WHERE mac = ?", (mac.lower(),),
    ).fetchone()
    return row is not None


def _ip_exists(conn: sqlite3.Connection, ip: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM assets WHERE primary_ip = ?", (ip,),
    ).fetchone()
    return row is not None


def import_lansweeper_csv(
    conn: sqlite3.Connection,
    *,
    csv_path: Path,
    now: datetime,
) -> ImportReport:
    """Parse a Lansweeper CSV export and insert its rows as assets.

    Returns the (imported, skipped) counts.
    """
    if not Path(csv_path).exists():
        raise FileNotFoundError(csv_path)

    imported = 0
    skipped = 0
    iso = now.isoformat(timespec="seconds")

    with Path(csv_path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = _normalise_headers(raw)
            hostname = _pick(row, _MAPPING["hostname"])
            ip = _pick(row, _MAPPING["primary_ip"])
            mac = _pick(row, _MAPPING["mac"])

            if not any((hostname, ip, mac)):
                skipped += 1
                continue

            # Idempotency + collision guard: don't silently merge or duplicate.
            if mac and _mac_exists(conn, mac):
                skipped += 1
                continue
            if ip and _ip_exists(conn, ip):
                skipped += 1
                continue

            description = _pick(row, _MAPPING["description"])
            vendor = _pick(row, _MAPPING["vendor"])
            device_type = _pick(row, _MAPPING["device_type"])

            # Insert with source='imported' and per-field 'imported' provenance.
            cur = conn.execute(
                "INSERT INTO assets ("
                "hostname, primary_ip, vendor, device_type, description, "
                "first_seen, last_seen, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'imported') RETURNING id",
                (hostname, ip, vendor, device_type, description, iso, iso),
            )
            asset_id = int(cur.fetchone()[0])

            for field_name, value in (
                ("hostname", hostname),
                ("primary_ip", ip),
                ("vendor", vendor),
                ("device_type", device_type),
                ("description", description),
            ):
                if value is not None:
                    conn.execute(
                        "INSERT INTO field_provenance "
                        "(asset_id, field, provenance, set_at) "
                        "VALUES (?, ?, 'imported', ?)",
                        (asset_id, field_name, iso),
                    )

            if mac:
                conn.execute(
                    "INSERT INTO mac_addresses (asset_id, mac, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?)",
                    (asset_id, mac.lower(), iso, iso),
                )

            imported += 1

    return ImportReport(imported=imported, skipped=skipped)
