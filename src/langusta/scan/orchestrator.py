"""Scan orchestrator.

Ties the discovery sources (ICMP, ARP for M2; rDNS + TCP + mDNS in M3;
SNMP in M5) together, runs them concurrently, and funnels each per-IP
observation through `db.writer.apply_scan_observation` — the single write
path that upholds the scanner-proposes-human-disposes invariant.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from langusta.db import scans as scans_dal
from langusta.db.writer import Deferred, Inserted, Observation, Updated, apply_scan_observation
from langusta.platform.base import PlatformBackend
from langusta.scan import oui as oui_module
from langusta.scan.arp import arp_lookup
from langusta.scan.icmp import PingResult, expand_target, ping_sweep
from langusta.scan.mdns import discover as mdns_discover
from langusta.scan.rdns import resolve_many
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


async def run_scan(
    conn: sqlite3.Connection,
    target: str,
    *,
    platform_backend: PlatformBackend,
    ping_fn: PingFn | None = None,
    now_fn: Clock | None = None,
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
    import asyncio as _asyncio
    rdns_task = _asyncio.create_task(resolve_many(alive_set))
    tcp_task = _asyncio.create_task(probe_ports_many(alive_set))
    mdns_task = _asyncio.create_task(mdns_discover(target_ips=alive_set))
    rdns_map, tcp_map, mdns_map = await _asyncio.gather(rdns_task, tcp_task, mdns_task)

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

        obs = Observation(
            primary_ip=ip,
            hostname=hostname,
            mac=mac,
            vendor=vendor,
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
