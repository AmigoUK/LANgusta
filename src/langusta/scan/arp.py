"""ARP-table enrichment of scan targets.

Reads the local ARP table via `PlatformBackend.arp_table()` and returns a
filtered {ip: mac} map for the target IPs. This is how ICMP-alive hosts get
paired with their MAC addresses for identity resolution.
"""

from __future__ import annotations

from langusta.core.models import normalize_mac
from langusta.platform.base import PlatformBackend


def arp_lookup(
    target_ips: set[str],
    *,
    backend: PlatformBackend,
) -> dict[str, str]:
    """Return {ip: mac} for the subset of `target_ips` present in the host's
    ARP table. MACs are normalised to lowercase."""
    if not target_ips:
        return {}

    found: dict[str, str] = {}
    for ip, mac in backend.arp_table():
        if ip in target_ips and ip not in found:
            found[ip] = normalize_mac(mac)
    return found
