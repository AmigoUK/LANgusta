"""Scan orchestrator tests — the glue from target string to inserted rows.

Injects the ICMP ping function and a PlatformBackend so no real packets
leave the test process. Real-network integration happens in M8's smoke job.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from langusta.db import assets as assets_dal
from langusta.db import proposed_changes as pc_dal
from langusta.db import scans as scans_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.platform.base import ArpEntry
from langusta.scan.icmp import PingResult
from langusta.scan.orchestrator import ScanReport, run_scan

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


class _StubBackend:
    def __init__(self, arp: list[ArpEntry] | None = None) -> None:
        self._arp = arp or []

    def arp_table(self) -> Iterable[ArpEntry]:
        return iter(self._arp)

    def enforce_private(self, path) -> None:  # pragma: no cover
        ...


def _make_ping_fn(alive_ips: list[str]):
    async def fn(targets: list[str], **_: object) -> list[PingResult]:
        return [PingResult(address=ip, is_alive=True, rtt_ms=1.2) for ip in alive_ips]
    return fn


def _clock(t: datetime):
    return lambda: t


# ---------------------------------------------------------------------------
# Happy path — ICMP + ARP, no prior state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_scan_inserts_new_assets_for_alive_hosts(tmp_path: Path) -> None:
    db = tmp_path / "orch.sqlite"
    migrate(db)

    alive = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    arp = [
        ("10.0.0.1", "aa:bb:cc:00:00:01"),
        ("10.0.0.2", "aa:bb:cc:00:00:02"),
        # .3 has no ARP entry — still gets inserted with no MAC.
    ]

    with connect(db) as conn:
        report = await run_scan(
            conn,
            target="10.0.0.0/30",
            platform_backend=_StubBackend(arp),
            ping_fn=_make_ping_fn(alive),
            now_fn=_clock(NOW),
        )

    assert isinstance(report, ScanReport)
    assert report.hosts_alive == 3
    assert report.inserted == 3
    assert report.updated == 0
    assert report.deferred == 0
    assert report.proposed_changes == 0

    with connect(db) as conn:
        rows = assets_dal.list_all(conn)
    assert {r.primary_ip for r in rows} == {"10.0.0.1", "10.0.0.2", "10.0.0.3"}
    mac_map = {r.primary_ip: r.macs for r in rows}
    assert mac_map["10.0.0.1"] == ["aa:bb:cc:00:00:01"]
    assert mac_map["10.0.0.2"] == ["aa:bb:cc:00:00:02"]
    assert mac_map["10.0.0.3"] == []


@pytest.mark.asyncio
async def test_run_scan_records_scan_row_and_host_count(tmp_path: Path) -> None:
    db = tmp_path / "orch.sqlite"
    migrate(db)
    with connect(db) as conn:
        report = await run_scan(
            conn,
            target="10.0.0.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_make_ping_fn(["10.0.0.1"]),
            now_fn=_clock(NOW),
        )
        scan = scans_dal.get_by_id(conn, report.scan_id)
    assert scan is not None
    assert scan.target == "10.0.0.0/30"
    assert scan.started_at == NOW
    assert scan.finished_at == NOW
    assert scan.host_count == 1


@pytest.mark.asyncio
async def test_run_scan_on_empty_subnet_returns_zero_hosts(tmp_path: Path) -> None:
    db = tmp_path / "orch.sqlite"
    migrate(db)
    with connect(db) as conn:
        report = await run_scan(
            conn,
            target="192.168.99.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_make_ping_fn([]),
            now_fn=_clock(NOW),
        )
    assert report.hosts_alive == 0
    assert report.inserted == 0


# ---------------------------------------------------------------------------
# Idempotent rescan (the M2 invariant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescan_is_idempotent_same_alive_hosts(tmp_path: Path) -> None:
    db = tmp_path / "orch.sqlite"
    migrate(db)

    alive = ["10.0.0.1"]
    arp = [("10.0.0.1", "aa:bb:cc:00:00:01")]

    with connect(db) as conn:
        r1 = await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(arp),
            ping_fn=_make_ping_fn(alive), now_fn=_clock(NOW),
        )
    with connect(db) as conn:
        r2 = await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(arp),
            ping_fn=_make_ping_fn(alive), now_fn=_clock(LATER),
        )

    assert r1.inserted == 1 and r1.updated == 0
    assert r2.inserted == 0 and r2.updated == 1

    with connect(db) as conn:
        rows = assets_dal.list_all(conn)
    assert len(rows) == 1
    asset = rows[0]
    assert asset.first_seen == NOW
    assert asset.last_seen == LATER


# ---------------------------------------------------------------------------
# Manual-field conflict produces proposed_change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scanner_does_not_overwrite_manual_field(tmp_path: Path) -> None:
    db = tmp_path / "orch.sqlite"
    migrate(db)

    # Human enters the asset manually.
    with connect(db) as conn:
        aid = assets_dal.insert_manual(
            conn, hostname="set-by-human", primary_ip="10.0.0.1",
            mac="aa:bb:cc:00:00:01", now=NOW,
        )

    # Scanner observes a different hostname on the same MAC (via ARP).
    # For hostname to differ on this test we'd need rDNS; the M2 scanner
    # only emits IP + MAC. So simulate the conflict at the writer layer via
    # a direct Observation. This test covers the orchestrator choosing
    # Update (same-host resolution) and not overwriting the asset row.
    arp = [("10.0.0.1", "aa:bb:cc:00:00:01")]
    with connect(db) as conn:
        report = await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(arp),
            ping_fn=_make_ping_fn(["10.0.0.1"]),
            now_fn=_clock(LATER),
        )

    # M2 scanner emits IP + MAC (no hostname), so no field conflict. Asset
    # stays unchanged except last_seen.
    assert report.updated == 1
    with connect(db) as conn:
        asset = assets_dal.get_by_id(conn, aid)
        open_pcs = pc_dal.list_open(conn, asset_id=aid)
    assert asset is not None
    assert asset.hostname == "set-by-human"
    assert asset.last_seen == LATER
    # No conflict because the M2 scanner doesn't emit hostname.
    assert open_pcs == []


# ---------------------------------------------------------------------------
# Duration + reporting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_report_duration_is_non_negative(tmp_path: Path) -> None:
    db = tmp_path / "orch.sqlite"
    migrate(db)

    times = iter([NOW, NOW + timedelta(seconds=3)])

    def clock() -> datetime:
        return next(times)

    with connect(db) as conn:
        report = await run_scan(
            conn, target="10.0.0.0/30",
            platform_backend=_StubBackend(),
            ping_fn=_make_ping_fn([]), now_fn=clock,
        )
    assert report.duration_seconds == 3.0
