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
from langusta.scan.arp import arp_lookup
from langusta.scan.icmp import PingResult, expand_target, ping_sweep

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

    arp_map = arp_lookup(set(alive_ips), backend=platform_backend)

    inserted = updated = deferred = proposed_total = 0

    for ip in alive_ips:
        obs = Observation(primary_ip=ip, mac=arp_map.get(ip))
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
