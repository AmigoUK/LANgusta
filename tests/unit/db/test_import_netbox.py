"""NetBox API importer tests.

Spec §7 Should-Have. Second migration on-ramp after Lansweeper. Injects
an HTTP fetcher so tests stay offline; production uses httpx.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.core.provenance import FieldProvenance
from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.import_netbox import (
    NetBoxAuthError,
    NetBoxNetworkError,
    import_netbox,
)
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _page(results, next_url=None, *, count=None):
    return {
        "count": count if count is not None else len(results),
        "next": next_url,
        "previous": None,
        "results": results,
    }


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "nb.sqlite"
    migrate(p)
    return p


def _device(
    name: str,
    *,
    ip: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    role: str | None = None,
) -> dict:
    """Build a NetBox-shaped device payload."""
    return {
        "id": hash(name) & 0x7fffffff,
        "name": name,
        "primary_ip4": {"address": f"{ip}/24"} if ip else None,
        "device_type": {
            "model": model,
            "manufacturer": {"name": manufacturer} if manufacturer else None,
        } if (manufacturer or model) else None,
        "role": {"name": role} if role else None,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_imports_devices_with_imported_provenance(db: Path) -> None:
    async def fake_get(url: str, *, token: str):
        assert token == "netbox-token"
        assert "dcim/devices" in url
        return _page([
            _device("router-01", ip="10.0.0.1",
                    manufacturer="Cisco", model="C3750"),
        ])

    with connect(db) as conn:
        report = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="netbox-token", now=NOW, http_get=fake_get,
        )
        [asset] = assets_dal.list_all(conn)
        prov = assets_dal.get_provenance(conn, asset.id)

    assert report.imported == 1
    assert report.skipped == 0
    assert asset.hostname == "router-01"
    assert asset.primary_ip == "10.0.0.1"
    assert asset.vendor == "Cisco"
    assert asset.device_type == "C3750"
    assert asset.source == "imported"
    assert prov["hostname"].provenance is FieldProvenance.IMPORTED
    assert prov["primary_ip"].provenance is FieldProvenance.IMPORTED


@pytest.mark.asyncio
async def test_follows_pagination(db: Path) -> None:
    pages = iter([
        _page(
            [_device(f"host-{i}", ip=f"10.0.0.{i + 1}") for i in range(3)],
            next_url="https://netbox.example.com/api/dcim/devices/?offset=3",
            count=5,
        ),
        _page(
            [_device(f"host-{i}", ip=f"10.0.0.{i + 1}") for i in range(3, 5)],
            next_url=None,
            count=5,
        ),
    ])

    async def fake_get(url: str, *, token: str):
        return next(pages)

    with connect(db) as conn:
        report = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )
        rows = assets_dal.list_all(conn)

    assert report.imported == 5
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_device_without_primary_ip_still_imports(db: Path) -> None:
    async def fake_get(url: str, *, token: str):
        return _page([_device("no-ip", ip=None)])

    with connect(db) as conn:
        report = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )
        [asset] = assets_dal.list_all(conn)
    assert report.imported == 1
    assert asset.hostname == "no-ip"
    assert asset.primary_ip is None


@pytest.mark.asyncio
async def test_device_without_name_is_skipped(db: Path) -> None:
    async def fake_get(url: str, *, token: str):
        return _page([
            {"id": 1, "name": None, "primary_ip4": None,
             "device_type": None, "role": None},
        ])

    with connect(db) as conn:
        report = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )
    assert report.imported == 0
    assert report.skipped == 1


# ---------------------------------------------------------------------------
# Collision handling (mirrors Lansweeper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_ip_skipped(db: Path) -> None:
    # Seed existing row.
    with connect(db) as conn:
        assets_dal.insert_manual(
            conn, hostname="already-here", primary_ip="10.0.0.1", now=NOW,
        )

    async def fake_get(url: str, *, token: str):
        return _page([_device("netbox-dup", ip="10.0.0.1")])

    with connect(db) as conn:
        report = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )
        rows = assets_dal.list_all(conn)

    assert report.imported == 0
    assert report.skipped == 1
    # Existing row untouched.
    assert [r.hostname for r in rows] == ["already-here"]


@pytest.mark.asyncio
async def test_second_run_idempotent(db: Path) -> None:
    async def fake_get(url: str, *, token: str):
        return _page([_device("nb-host", ip="10.0.0.1")])

    with connect(db) as conn:
        first = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )
    with connect(db) as conn:
        second = await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )
    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped == 1


# ---------------------------------------------------------------------------
# Error surfaces
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_raises_auth_error(db: Path) -> None:
    async def fake_get(url: str, *, token: str):
        from langusta.db.import_netbox import NetBoxAuthError
        raise NetBoxAuthError("401 Unauthorized")

    with connect(db) as conn, pytest.raises(NetBoxAuthError):
        await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="bad", now=NOW, http_get=fake_get,
        )


@pytest.mark.asyncio
async def test_network_error_raises_network_error(db: Path) -> None:
    async def fake_get(url: str, *, token: str):
        from langusta.db.import_netbox import NetBoxNetworkError
        raise NetBoxNetworkError("connection refused")

    with connect(db) as conn, pytest.raises(NetBoxNetworkError):
        await import_netbox(
            conn, base_url="https://netbox.example.com",
            token="t", now=NOW, http_get=fake_get,
        )


# ---------------------------------------------------------------------------
# URL shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_url_is_devices_endpoint(db: Path) -> None:
    seen: list[str] = []

    async def fake_get(url: str, *, token: str):
        seen.append(url)
        return _page([])

    with connect(db) as conn:
        await import_netbox(
            conn, base_url="https://netbox.example.com/",
            token="t", now=NOW, http_get=fake_get,
        )
    assert seen[0].rstrip("/").endswith("/api/dcim/devices")


@pytest.mark.asyncio
async def test_trailing_slash_in_base_url_tolerated(db: Path) -> None:
    seen: list[str] = []

    async def fake_get(url: str, *, token: str):
        seen.append(url)
        return _page([])

    with connect(db) as conn:
        await import_netbox(
            conn, base_url="https://netbox.example.com/",
            token="t", now=NOW, http_get=fake_get,
        )
    # No double slash before /api/.
    assert "//api/" not in seen[0]
