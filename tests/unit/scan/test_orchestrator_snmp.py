"""Orchestrator SNMP integration — sys_descr lands on detected_os.

ADR-0003: SNMP is optional; hosts that don't answer are marked
`snmp:unavailable` in the log but don't fail the scan.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.platform.base import ArpEntry
from langusta.scan.icmp import PingResult
from langusta.scan.orchestrator import run_scan
from langusta.scan.snmp.auth import SnmpV2cAuth
from langusta.scan.snmp.transcript_backend import TranscriptBackend

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


class _StubBackend:
    def __init__(self, arp: list[ArpEntry] | None = None) -> None:
        self._arp = arp or []

    def arp_table(self) -> Iterable[ArpEntry]:
        return iter(self._arp)

    def enforce_private(self, path) -> None:  # pragma: no cover
        ...


def _ping(alive_ips: list[str]):
    async def fn(targets, **_):
        return [PingResult(address=ip, is_alive=True, rtt_ms=1.0) for ip in alive_ips]
    return fn


@pytest.mark.asyncio
async def test_snmp_sys_descr_populates_detected_os(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    migrate(db)
    client = TranscriptBackend.from_dict({
        "10.0.0.1": {"sys_descr": "Cisco IOS 15.2(4)E9"},
    })

    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_ping(["10.0.0.1"]),
            now_fn=lambda: NOW,
            snmp_client=client,
            snmp_auth=SnmpV2cAuth(community="public"),
        )
        [asset] = assets_dal.list_all(conn)
    assert asset.detected_os == "Cisco IOS 15.2(4)E9"


@pytest.mark.asyncio
async def test_snmp_unreachable_does_not_fail_scan(tmp_path: Path) -> None:
    """If a host doesn't answer SNMP, the scan must still succeed and the
    host gets inserted without a detected_os."""
    db = tmp_path / "s.sqlite"
    migrate(db)
    client = TranscriptBackend.from_dict({})  # no host responds

    with connect(db) as conn:
        report = await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_ping(["10.0.0.1", "10.0.0.2"]),
            now_fn=lambda: NOW,
            snmp_client=client,
            snmp_auth=SnmpV2cAuth(community="public"),
        )
        rows = assets_dal.list_all(conn)
    assert report.inserted == 2
    assert all(a.detected_os is None for a in rows)


@pytest.mark.asyncio
async def test_no_snmp_client_no_enrichment(tmp_path: Path) -> None:
    db = tmp_path / "s.sqlite"
    migrate(db)
    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_ping(["10.0.0.1"]),
            now_fn=lambda: NOW,
            # snmp_client / snmp_auth not provided
        )
        [asset] = assets_dal.list_all(conn)
    assert asset.detected_os is None


@pytest.mark.asyncio
async def test_snmp_only_queries_alive_hosts(tmp_path: Path) -> None:
    """Dead hosts aren't ICMP-alive so we don't SNMP them either."""
    db = tmp_path / "s.sqlite"
    migrate(db)
    # Only 10.0.0.1 is alive; transcript also covers .99 but it's never queried.
    client = TranscriptBackend.from_dict({
        "10.0.0.1": {"sys_descr": "Alive"},
        "10.0.0.99": {"sys_descr": "Should not be used"},
    })

    with connect(db) as conn:
        await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_ping(["10.0.0.1"]),
            now_fn=lambda: NOW,
            snmp_client=client,
            snmp_auth=SnmpV2cAuth(community="public"),
        )
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1
    assert rows[0].detected_os == "Alive"
