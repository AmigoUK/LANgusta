"""NetBox API importer.

Spec §7 Should-Have. Reads /api/dcim/devices/ with token auth and maps
the device records to LANgusta assets. Rows land with
source='imported' + provenance='imported' so later scans can't silently
overwrite them.

HTTP access is injectable so unit tests stay offline; production callers
use `default_http_get` which is a thin httpx wrapper.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ImportReport:
    imported: int
    skipped: int


class NetBoxAuthError(RuntimeError):
    """NetBox rejected the token (401 / 403)."""


class NetBoxNetworkError(RuntimeError):
    """Couldn't reach NetBox (DNS, connect, timeout, 5xx)."""


# (url, token=...) -> dict in NetBox's paginated-list shape.
HttpGet = Callable[..., Awaitable[dict]]


async def default_http_get(url: str, *, token: str) -> dict:
    """Real HTTP fetch via httpx. Translates 401/403/5xx to our error types."""
    import httpx

    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise NetBoxNetworkError(str(exc)) from exc

    if resp.status_code in (401, 403):
        raise NetBoxAuthError(f"{resp.status_code} {resp.reason_phrase}")
    if resp.status_code >= 500:
        raise NetBoxNetworkError(f"{resp.status_code} {resp.reason_phrase}")
    resp.raise_for_status()
    return resp.json()


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/dcim/devices"


def _extract_ip(device: dict) -> str | None:
    """NetBox stores primary_ip4 as {"address": "192.168.1.1/24"}."""
    ip4 = device.get("primary_ip4")
    if not ip4:
        return None
    raw = ip4.get("address")
    if not raw:
        return None
    return raw.split("/", 1)[0]


def _extract_vendor(device: dict) -> str | None:
    dt = device.get("device_type")
    if not dt:
        return None
    mfr = dt.get("manufacturer")
    if not mfr:
        return None
    return mfr.get("name")


def _extract_model(device: dict) -> str | None:
    dt = device.get("device_type")
    return dt.get("model") if dt else None


def _mac_exists(conn: sqlite3.Connection, mac: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM mac_addresses WHERE mac = ?", (mac.lower(),),
    ).fetchone() is not None


def _ip_exists(conn: sqlite3.Connection, ip: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM assets WHERE primary_ip = ?", (ip,),
    ).fetchone() is not None


async def import_netbox(
    conn: sqlite3.Connection,
    *,
    base_url: str,
    token: str,
    now: datetime,
    http_get: HttpGet | None = None,
) -> ImportReport:
    """Import every device from NetBox's /api/dcim/devices/ endpoint.

    Existing assets at a colliding primary_ip are left untouched
    (reported as skipped). Pagination follows `next` URLs until exhausted.
    """
    getter: HttpGet = http_get if http_get is not None else default_http_get
    iso = now.isoformat(timespec="seconds")

    imported = 0
    skipped = 0
    url: str | None = _endpoint(base_url)

    while url is not None:
        page = await getter(url, token=token)
        for device in page.get("results", []):
            hostname = device.get("name")
            ip = _extract_ip(device)

            if not hostname and not ip:
                skipped += 1
                continue

            if ip and _ip_exists(conn, ip):
                skipped += 1
                continue

            vendor = _extract_vendor(device)
            model = _extract_model(device)

            cur = conn.execute(
                "INSERT INTO assets ("
                "hostname, primary_ip, vendor, device_type, "
                "first_seen, last_seen, source"
                ") VALUES (?, ?, ?, ?, ?, ?, 'imported') RETURNING id",
                (hostname, ip, vendor, model, iso, iso),
            )
            asset_id = int(cur.fetchone()[0])

            for field_name, value in (
                ("hostname", hostname),
                ("primary_ip", ip),
                ("vendor", vendor),
                ("device_type", model),
            ):
                if value is not None:
                    conn.execute(
                        "INSERT INTO field_provenance "
                        "(asset_id, field, provenance, set_at) "
                        "VALUES (?, ?, 'imported', ?)",
                        (asset_id, field_name, iso),
                    )

            imported += 1

        url = page.get("next")

    return ImportReport(imported=imported, skipped=skipped)
