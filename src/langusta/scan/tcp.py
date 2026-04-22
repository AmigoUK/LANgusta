"""TCP port probe.

stdlib-only (per spec §6 — no icmplib-like third-party dep). For each
(ip, port) we try `asyncio.open_connection` with a short timeout; success
means the port is open. We never send bytes, never parse banners, and
close the connection immediately.

Top-port list is curated for home/enterprise LAN relevance: default SSH,
HTTP/S, RDP, SMB, SNMP-TCP (rare), DNS, ESXi, common web admin ports,
printer stacks. Users can override per-call via `ports=`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

# Curated top ports (~45). Not nmap's 1000 — LANgusta aims at LAN-relevant
# admin surface, not attack-surface enumeration.
DEFAULT_TOP_PORTS: tuple[int, ...] = (
    21,    # FTP
    22,    # SSH
    23,    # Telnet
    25,    # SMTP
    53,    # DNS (TCP fallback)
    80,    # HTTP
    88,    # Kerberos
    110,   # POP3
    111,   # RPC
    135,   # DCE/RPC
    139,   # NetBIOS
    143,   # IMAP
    161,   # SNMP (rare TCP)
    389,   # LDAP
    443,   # HTTPS
    445,   # SMB
    465,   # SMTPS
    515,   # LPD (printer)
    548,   # AFP
    587,   # SMTP submission
    631,   # IPP (printer)
    636,   # LDAPS
    873,   # rsync
    902,   # VMware ESXi
    993,   # IMAPS
    995,   # POP3S
    1433,  # MSSQL
    2049,  # NFS
    3306,  # MySQL
    3389,  # RDP
    5000,  # UPnP / Synology
    5432,  # Postgres
    5900,  # VNC
    5985,  # WinRM HTTP
    6379,  # Redis
    7000,  # Web UI
    8000,  # Web alt
    8008,  # Web alt
    8080,  # Web proxy
    8081,  # Web alt
    8443,  # HTTPS alt
    8888,  # Web alt
    9090,  # Web admin (Prometheus, Cockpit)
    9100,  # JetDirect printer
    32400, # Plex
)


# Module-level alias so tests can `monkeypatch.setattr("langusta.scan.
# tcp._open_connection", fake)`. Delegates to the shared helper in
# core.net to avoid the twin duplicate that was living in
# monitor/checks/tcp.py (Wave-3 A-009).
from langusta.core.net import open_tcp_connection as _open_connection  # noqa: E402


async def _probe_one(ip: str, port: int, *, timeout: float) -> int | None:
    import contextlib

    try:
        _, writer = await _open_connection(ip, port, timeout=timeout)
    except (OSError, TimeoutError):
        return None
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return port


async def probe_ports(
    ip: str,
    *,
    ports: Iterable[int] = DEFAULT_TOP_PORTS,
    timeout: float = 0.8,
    concurrent: int = 32,
) -> frozenset[int]:
    """Return the set of `ports` that accept a TCP connection on `ip`."""
    port_list = list(ports)
    if not port_list:
        return frozenset()

    sem = asyncio.Semaphore(concurrent)

    async def guarded(p: int) -> int | None:
        async with sem:
            return await _probe_one(ip, p, timeout=timeout)

    results = await asyncio.gather(*(guarded(p) for p in port_list))
    return frozenset(p for p in results if p is not None)


async def probe_ports_many(
    ips: Iterable[str],
    *,
    ports: Iterable[int] = DEFAULT_TOP_PORTS,
    timeout: float = 0.8,
    concurrent_per_host: int = 32,
) -> dict[str, frozenset[int]]:
    """Probe each IP concurrently. Hosts with no open ports are omitted."""
    ip_list = list(ips)
    if not ip_list:
        return {}
    results = await asyncio.gather(
        *(
            probe_ports(ip, ports=ports, timeout=timeout, concurrent=concurrent_per_host)
            for ip in ip_list
        )
    )
    return {ip: open_ports for ip, open_ports in zip(ip_list, results, strict=True) if open_ports}
