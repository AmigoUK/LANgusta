"""Lansweeper CSV importer.

Spec §7: the migration on-ramp for users leaving Lansweeper or NetBox.

Every mapped row is routed through
`langusta.db.import_common.apply_imported_observation`, so import obeys the
same scanner-proposes-human-disposes invariant as scans:

  * MAC-matched rows merge into the existing asset; protected-field
    conflicts become `proposed_changes` instead of silent overwrites.
  * IP-only matches (or rows where MAC and IP disagree) land in the
    `review_queue` for human disposition.
  * Unmatched rows insert with source='imported' and per-field IMPORTED
    provenance so later scans cannot silently overwrite them.

Per-row failures do not abort the import: every row executes inside a
`SAVEPOINT row_<n>` so parse/validation/DB errors roll that row back and
surface as a `RowError` in the `ImportReport`. `dry_run=True` wraps the
entire pass in a SAVEPOINT that is unconditionally rolled back.
"""

from __future__ import annotations

import csv
import ipaddress
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from langusta.db.import_common import (
    Deferred,
    Inserted,
    RowError,
    Updated,
    apply_imported_observation,
)


@dataclass(frozen=True, slots=True)
class ImportReport:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    proposed_changes_created: int = 0
    review_queue_entries: int = 0
    row_errors: tuple[RowError, ...] = field(default_factory=tuple)


# Field-mapping: LANgusta asset field -> tuple of lower-cased Lansweeper
# column candidates, tried in order. Matching is case-insensitive.
_MAPPING: dict[str, tuple[str, ...]] = {
    "hostname":       ("assetname", "name"),
    "primary_ip":     ("ipaddress", "ip", "ipv4"),
    "mac":            ("mac", "macaddress", "macaddresses"),
    "description":    ("description", "notes"),
    "vendor":         ("manufacturer", "vendor"),
    "device_type":    ("type", "assettype", "model"),
    "detected_os":    ("operatingsystem", "os", "osname"),
    "location":       ("location", "site", "building"),
    "owner":          ("owner", "assigneduser", "assignedto", "user"),
    "management_url": ("url", "managementurl", "weburl"),
}


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        value = row.get(name)
        if value:
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _normalise_headers(row: dict[str, str]) -> dict[str, str]:
    """Lowercase keys so the import is tolerant of ASSETNAME / AssetName.

    Also strips a BOM from the first header — Excel-exported CSVs frequently
    prepend U+FEFF to the first cell, which breaks naïve dict lookups.
    """
    out: dict[str, str] = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).lstrip("\ufeff").strip().lower()
        out[key] = v if v is not None else ""
    return out


def _extract_fields(
    row: dict[str, str],
) -> tuple[dict[str, str], str | None]:
    """Return (importable_fields, mac). `mac` is normalised to lowercase."""
    fields_out: dict[str, str] = {}
    for target, candidates in _MAPPING.items():
        if target == "mac":
            continue
        value = _pick(row, candidates)
        if value is not None:
            fields_out[target] = value
    mac_raw = _pick(row, _MAPPING["mac"])
    mac = mac_raw.lower() if mac_raw else None
    return fields_out, mac


def import_lansweeper_csv(
    conn: sqlite3.Connection,
    *,
    csv_path: Path,
    now: datetime,
    dry_run: bool = False,
) -> ImportReport:
    """Parse a Lansweeper CSV export and merge its rows into the inventory.

    Returns an `ImportReport` summarising the per-row outcomes. When
    `dry_run=True` every write is rolled back before returning; the counts
    still reflect what would have happened.
    """
    if not Path(csv_path).exists():
        raise FileNotFoundError(csv_path)

    imported = 0
    updated = 0
    skipped = 0
    proposed_changes_created = 0
    review_queue_entries = 0
    row_errors: list[RowError] = []

    conn.execute("SAVEPOINT import_lansweeper")
    try:
        with Path(csv_path).open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                row = _normalise_headers(raw)
                line_number = reader.line_num
                fields_out, mac = _extract_fields(row)

                # Without at least one of (hostname, primary_ip, mac) the row
                # has no identity anchor — merging by description alone would
                # create garbage. Skip without recording an error.
                if (
                    fields_out.get("hostname") is None
                    and fields_out.get("primary_ip") is None
                    and mac is None
                ):
                    skipped += 1
                    continue

                # Validate IP before opening a per-row savepoint — if the row
                # is malformed we don't want any writes to attempt.
                ip = fields_out.get("primary_ip")
                if ip is not None:
                    try:
                        ipaddress.ip_address(ip)
                    except ValueError as exc:
                        skipped += 1
                        row_errors.append(
                            RowError(
                                line_number=line_number,
                                reason=f"invalid IP {ip!r}: {exc}",
                                raw=dict(raw),
                            )
                        )
                        continue

                savepoint = f"row_{line_number}"
                conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    outcome = apply_imported_observation(
                        conn, fields=fields_out, mac=mac, now=now,
                    )
                except Exception as exc:
                    conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                    skipped += 1
                    row_errors.append(
                        RowError(
                            line_number=line_number,
                            reason=repr(exc),
                            raw=dict(raw),
                        )
                    )
                    continue
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")

                if isinstance(outcome, Inserted):
                    imported += 1
                elif isinstance(outcome, Updated):
                    updated += 1
                    proposed_changes_created += outcome.proposed_changes
                elif isinstance(outcome, Deferred):
                    review_queue_entries += 1
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT import_lansweeper")
        conn.execute("RELEASE SAVEPOINT import_lansweeper")
        raise

    if dry_run:
        conn.execute("ROLLBACK TO SAVEPOINT import_lansweeper")
    conn.execute("RELEASE SAVEPOINT import_lansweeper")

    return ImportReport(
        imported=imported,
        updated=updated,
        skipped=skipped,
        proposed_changes_created=proposed_changes_created,
        review_queue_entries=review_queue_entries,
        row_errors=tuple(row_errors),
    )
