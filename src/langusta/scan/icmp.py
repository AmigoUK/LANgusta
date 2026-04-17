"""ICMP sweep.

Wraps `icmplib.async_multiping` in unprivileged mode so LANgusta doesn't
require CAP_NET_RAW or root on Linux. On macOS, unprivileged ICMP uses
ICMP datagram sockets available to every user.

Spec: docs/specs/02-tech-stack-and-architecture.md §6.
ADR-0004: works cross-platform without special permissions.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from icmplib import async_multiping as _async_multiping


@dataclass(frozen=True, slots=True)
class PingResult:
    address: str
    is_alive: bool
    rtt_ms: float | None


def expand_target(target: str) -> list[str]:
    """Expand a single IP, CIDR, or host into a list of usable IPv4 addresses.

    For networks with a prefix shorter than /32, network and broadcast
    addresses are excluded — we don't ping them. /32 yields one address.

    Raises ValueError for IPv6 inputs (post-v1) or unparseable strings.
    """
    try:
        network = ipaddress.ip_network(target, strict=False)
    except ValueError:
        try:
            addr = ipaddress.ip_address(target)
        except ValueError as exc:
            raise ValueError(f"unparseable target {target!r}") from exc
        if isinstance(addr, ipaddress.IPv6Address):
            raise ValueError("IPv6 is not supported in v1") from None
        return [str(addr)]

    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("IPv6 is not supported in v1")

    if network.prefixlen == 32:
        return [str(network.network_address)]

    return [str(h) for h in network.hosts()]


async def ping_sweep(
    targets: list[str],
    *,
    count: int = 1,
    timeout: float = 1.0,
    concurrent: int = 64,
) -> list[PingResult]:
    """Ping every target; return only the alive ones."""
    if not targets:
        return []
    hosts = await _async_multiping(
        targets,
        count=count,
        timeout=timeout,
        concurrent_tasks=concurrent,
        privileged=False,
    )
    return [
        PingResult(address=h.address, is_alive=True, rtt_ms=h.avg_rtt)
        for h in hosts
        if h.is_alive
    ]
