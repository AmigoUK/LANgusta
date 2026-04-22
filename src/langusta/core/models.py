"""Core domain dataclasses.

Stdlib-only (ADR-0001). These types are returned by the DAL and consumed by
the TUI, CLI, scanner, and monitor — a single language across layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Asset:
    """A single device with all v1 identity + human fields.

    Field-level provenance is stored separately (see core/provenance.py);
    callers who need it call `db.assets.get_provenance(conn, asset_id)`.
    """

    id: int
    hostname: str | None
    primary_ip: str | None
    vendor: str | None
    detected_os: str | None
    device_type: str | None
    description: str | None
    location: str | None
    owner: str | None
    management_url: str | None
    criticality: str | None
    first_seen: datetime
    last_seen: datetime
    source: str  # 'scanned' | 'manual' | 'imported'
    macs: list[str] = field(default_factory=list)


def normalize_mac(mac: str) -> str:
    """Return the canonical storage form of a MAC address.

    LANgusta stores MAC addresses lowercase, preserving the caller's
    separator (colons, dashes, etc.). Every DAL/ingestion site that
    persists or looks up a MAC routes through here so the
    normalisation lives in one place (Wave-3 finding A-013).
    """
    return mac.lower()
