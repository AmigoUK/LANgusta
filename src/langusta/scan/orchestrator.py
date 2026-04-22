"""Scan orchestrator.

Ties the discovery sources (ICMP, ARP for M2; rDNS + TCP + mDNS in M3;
SNMP in M5) together, runs them concurrently, and funnels each per-IP
observation through `db.writer.apply_scan_observation` — the single write
path that upholds the scanner-proposes-human-disposes invariant.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from langusta import backup as backup_mod
from langusta.db import scans as scans_dal
from langusta.db.writer import Deferred, Inserted, Observation, Updated, apply_scan_observation
from langusta.platform.base import PlatformBackend
from langusta.scan import oui as oui_module
from langusta.scan.arp import arp_lookup
from langusta.scan.icmp import PingResult, expand_target, ping_sweep
from langusta.scan.mdns import discover as mdns_discover
from langusta.scan.rdns import resolve_many
from langusta.scan.snmp.auth import SnmpAuth
from langusta.scan.snmp.client import SnmpClient
from langusta.scan.tcp import probe_ports_many

PingFn = Callable[[list[str]], Awaitable[list[PingResult]]]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class ScanReport:
    scan_id: int
    target: str
    hosts_probed: int
    hosts_alive: int
    inserted: int
    updated: int
    deferred: int
    proposed_changes: int
    duration_seconds: float


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _sqlite_path_for(conn: sqlite3.Connection) -> Path | None:
    """Extract the on-disk path of the main database from a live connection."""
    for row in conn.execute("PRAGMA database_list").fetchall():
        if row["name"] == "main":
            file = row["file"]
            return Path(file) if file else None
    return None


async def run_scan(
    conn: sqlite3.Connection,
    target: str,
    *,
    platform_backend: PlatformBackend,
    ping_fn: PingFn | None = None,
    now_fn: Clock | None = None,
    snmp_client: SnmpClient | None = None,
    snmp_auth: SnmpAuth | None = None,
    backups_dir: Path | None = None,
) -> ScanReport:
    """Run one end-to-end scan against `target`.

    Steps:
      1. Expand `target` (IP or CIDR) into a list of probes.
      2. ICMP-sweep in parallel; collect alive IPs.
      3. Consult the local ARP table for MACs on the alive IPs.
      4. Build one `Observation` per alive IP and dispatch through the
         TimelineWriter. Each dispatch is atomic; the enclosing caller owns
         the transaction (via `connect()` already).
      5. Record the scan row with host_count.
    """
    # Resolve defaults here (not in the signature) so `monkeypatch.setattr`
    # on this module's `ping_sweep` takes effect for the current call.
    effective_ping = ping_fn if ping_fn is not None else ping_sweep
    effective_clock = now_fn if now_fn is not None else _default_clock

    start = effective_clock()
    scan_id = scans_dal.start_scan(conn, target=target, now=start)

    probes = expand_target(target)
    alive_results = await effective_ping(probes) if probes else []
    alive_ips = [r.address for r in alive_results]
    alive_set = set(alive_ips)

    arp_map = arp_lookup(alive_set, backend=platform_backend)

    # Run enrichment stages concurrently against alive hosts.
    rdns_task = asyncio.create_task(resolve_many(alive_set))
    tcp_task = asyncio.create_task(probe_ports_many(alive_set))
    mdns_task = asyncio.create_task(mdns_discover(target_ips=alive_set))

    snmp_map: dict[str, str] = {}
    if snmp_client is not None and snmp_auth is not None and alive_set:
        async def _snmp_one(ip: str) -> tuple[str, str | None]:
            try:
                sys_descr = await snmp_client.get_sys_descr(ip, auth=snmp_auth)
            except Exception:
                sys_descr = None
            return ip, sys_descr

        snmp_gather = asyncio.gather(*(_snmp_one(ip) for ip in alive_ips))
        rdns_map, tcp_map, mdns_map, snmp_results = await asyncio.gather(
            rdns_task, tcp_task, mdns_task, snmp_gather,
        )
        for ip, sys_descr in snmp_results:
            if sys_descr:
                snmp_map[ip] = sys_descr
    else:
        rdns_map, tcp_map, mdns_map = await asyncio.gather(rdns_task, tcp_task, mdns_task)

    inserted = updated = deferred = proposed_total = 0

    for ip in alive_ips:
        mac = arp_map.get(ip)
        hostname = rdns_map.get(ip) or mdns_map.get(ip)
        vendor: str | None = None
        if mac is not None:
            try:
                vendor = oui_module.lookup(mac)
            except oui_module.InvalidMac:
                vendor = None
        open_ports = tcp_map.get(ip, frozenset())
        detected_os = snmp_map.get(ip)

        obs = Observation(
            primary_ip=ip,
            hostname=hostname,
            mac=mac,
            vendor=vendor,
            detected_os=detected_os,
            open_ports=open_ports,
        )
        outcome = apply_scan_observation(conn, obs, scan_id=scan_id, now=start)
        if isinstance(outcome, Inserted):
            inserted += 1
        elif isinstance(outcome, Updated):
            updated += 1
            proposed_total += outcome.proposed_changes
        elif isinstance(outcome, Deferred):
            deferred += 1

    end = effective_clock()
    scans_dal.finish_scan(conn, scan_id, host_count=len(alive_ips), now=end)

    # Post-scan backup (spec §9). Dedupe window of 1h prevents spam during
    # rapid scan cycles. Commit the current connection first so the backup
    # picks up the scan's writes.
    if backups_dir is not None:
        conn.commit()
        sqlite_path = _sqlite_path_for(conn)
        if sqlite_path is not None:
            backup_mod.write(sqlite_path, backups_dir, now=end)

    return ScanReport(
        scan_id=scan_id,
        target=target,
        hosts_probed=len(probes),
        hosts_alive=len(alive_ips),
        inserted=inserted,
        updated=updated,
        deferred=deferred,
        proposed_changes=proposed_total,
        duration_seconds=(end - start).total_seconds(),
    )
