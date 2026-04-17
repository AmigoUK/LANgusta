"""Orchestrator tests for M3 enrichments — rDNS, OUI, TCP, mDNS.

The M2 test file covers the base ICMP+ARP pipeline. Here we confirm the
extra discovery sources flow into the Observation and land on the asset.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import timeline as tl_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.platform.base import ArpEntry
from langusta.scan.icmp import PingResult
from langusta.scan.orchestrator import run_scan

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


class _StubBackend:
    def __init__(self, arp: list[ArpEntry]) -> None:
        self._arp = arp

    def arp_table(self) -> Iterable[ArpEntry]:
        return iter(self._arp)

    def enforce_private(self, path) -> None:  # pragma: no cover
        ...


def _ping(alive_ips: list[str]):
    async def fn(targets, **_):
        return [PingResult(address=ip, is_alive=True, rtt_ms=1.0) for ip in alive_ips]
    return fn


# ---------------------------------------------------------------------------
# rDNS enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rdns_hostname_lands_on_inserted_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "enr.sqlite"
    migrate(db)

    async def fake_rdns(ips, **_):
        return {"10.0.0.1": "router.internal"}

    monkeypatch.setattr("langusta.scan.orchestrator.resolve_many", fake_rdns)

    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend([("10.0.0.1", "aa:bb:cc:00:00:01")]),
            ping_fn=_ping(["10.0.0.1"]),
            now_fn=lambda: NOW,
        )
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1
    assert rows[0].hostname == "router.internal"


# ---------------------------------------------------------------------------
# OUI enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oui_vendor_lands_on_inserted_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "enr.sqlite"
    migrate(db)
    # 00:1B:54 → Cisco per the shipped OUI fixture.
    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend([("10.0.0.1", "00:1b:54:ab:cd:ef")]),
            ping_fn=_ping(["10.0.0.1"]),
            now_fn=lambda: NOW,
        )
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1
    assert rows[0].vendor == "Cisco Systems, Inc"


# ---------------------------------------------------------------------------
# TCP port enrichment -> timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_ports_appear_in_scan_diff_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "enr.sqlite"
    migrate(db)

    async def fake_tcp(ips, **_):
        return {"10.0.0.1": frozenset({22, 80, 443})}

    monkeypatch.setattr("langusta.scan.orchestrator.probe_ports_many", fake_tcp)

    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend([]),
            ping_fn=_ping(["10.0.0.1"]),
            now_fn=lambda: NOW,
        )
        [asset] = assets_dal.list_all(conn)
        entries = tl_dal.list_by_asset(conn, asset.id)

    # The 'system' entry from insert should be joined by a port-report
    # entry of kind scan_diff with the ports named in the body.
    kinds = [e.kind for e in entries]
    assert "scan_diff" in kinds
    diff = next(e for e in entries if e.kind == "scan_diff")
    body = diff.body
    assert "22" in body
    assert "80" in body
    assert "443" in body


# ---------------------------------------------------------------------------
# mDNS enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mdns_name_wins_when_rdns_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "enr.sqlite"
    migrate(db)

    async def no_rdns(ips, **_):
        return {}

    async def mdns(target_ips=None, **_):
        return {"10.0.0.2": "apple-tv.local"}

    monkeypatch.setattr("langusta.scan.orchestrator.resolve_many", no_rdns)
    monkeypatch.setattr("langusta.scan.orchestrator.mdns_discover", mdns)

    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend([]),
            ping_fn=_ping(["10.0.0.2"]),
            now_fn=lambda: NOW,
        )
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1
    assert rows[0].hostname == "apple-tv.local"
