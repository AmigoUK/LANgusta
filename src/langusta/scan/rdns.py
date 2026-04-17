"""Reverse DNS enrichment.

Small async wrapper over `socket.gethostbyaddr` — each resolution runs in a
thread so a slow DNS server can't stall the scan. We time out fast (default
1s) and treat every failure mode (NXDOMAIN, gaierror, timeout) as
"no hostname".
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterable

# Alias so tests can monkeypatch without reaching into stdlib.
_gethostbyaddr = socket.gethostbyaddr


async def resolve_one(ip: str, *, timeout: float = 1.0) -> str | None:
    """Return the hostname for an IP, or None if resolution fails or times out."""

    def _call() -> str:
        return _gethostbyaddr(ip)[0]

    try:
        return await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout)
    except (TimeoutError, socket.herror, socket.gaierror, OSError):
        return None


async def resolve_many(
    ips: Iterable[str], *, timeout: float = 1.0
) -> dict[str, str]:
    """Resolve every IP concurrently; return {ip: hostname} for those that
    resolved."""
    ip_list = list(ips)
    if not ip_list:
        return {}
    names = await asyncio.gather(
        *(resolve_one(ip, timeout=timeout) for ip in ip_list)
    )
    return {ip: name for ip, name in zip(ip_list, names, strict=True) if name}
