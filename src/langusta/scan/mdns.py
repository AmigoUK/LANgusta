"""mDNS / Bonjour discovery.

Listens for mDNS service announcements on the local segment for a bounded
window, collects (ip, name) pairs, and returns them as {ip: name}. Users
of the scanner pass the live IPs they care about; we filter.

The zeroconf integration is wrapped behind `browser_fn` so unit tests stay
offline. A real-network integration test lives as `@pytest.mark.integration`.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MdnsRecord:
    ip: str
    name: str  # e.g., "router.local"
    service_type: str


# Curated service types LAN devices commonly advertise.
DEFAULT_SERVICE_TYPES: tuple[str, ...] = (
    "_workstation._tcp.local.",
    "_ssh._tcp.local.",
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_ipp._tcp.local.",
    "_printer._tcp.local.",
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
    "_smb._tcp.local.",
    "_device-info._tcp.local.",
    "_googlecast._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_hap._tcp.local.",           # HomeKit
)


BrowserFn = Callable[[float], Awaitable[list[MdnsRecord]]]


async def _real_browse(timeout: float) -> list[MdnsRecord]:
    """Real zeroconf browser — listens for DEFAULT_SERVICE_TYPES during `timeout`."""
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: dict[tuple[str, str], MdnsRecord] = {}
    aiozc = AsyncZeroconf()

    # zeroconf calls handlers with keyword arguments; the zeroconf-instance
    # kwarg name shifted (old `zc=`, now `zeroconf=` in 0.148). Accept any
    # kwargs and rely on the `aiozc` bound in closure rather than whatever
    # the callback received, so we're compatible across the pinned range
    # (>=0.130).
    async def on_service_change(**kwargs: object) -> None:
        service_type = kwargs.get("service_type")
        name = kwargs.get("name")
        if not isinstance(service_type, str) or not isinstance(name, str):
            return
        info = AsyncServiceInfo(service_type, name)
        await info.async_request(aiozc.zeroconf, 1000)
        if not info.addresses:
            return
        for addr_bytes in info.addresses:
            try:
                ip = str(ipaddress.ip_address(addr_bytes))
            except ValueError:
                continue
            if ":" in ip:
                continue  # IPv6 — post-v1
            server = info.server or name
            key = (ip, service_type)
            if key not in found:
                found[key] = MdnsRecord(
                    ip=ip, name=server.rstrip("."), service_type=service_type,
                )

    try:
        browser = AsyncServiceBrowser(
            aiozc.zeroconf,
            list(DEFAULT_SERVICE_TYPES),
            handlers=[on_service_change],
        )
        try:
            await asyncio.sleep(timeout)
        finally:
            await browser.async_cancel()
    finally:
        await aiozc.async_close()

    # Dedup: keep the first record per IP.
    by_ip: dict[str, MdnsRecord] = {}
    for rec in found.values():
        by_ip.setdefault(rec.ip, rec)
    return list(by_ip.values())


async def discover(
    target_ips: Iterable[str] | None = None,
    *,
    timeout: float = 2.0,
    browser_fn: BrowserFn | None = None,
) -> dict[str, str]:
    """Return {ip: name} for devices seen on the LAN via mDNS.

    If `target_ips` is None, no IP filter is applied. If it's an empty
    collection, returns immediately without browsing.
    """
    targets: set[str] | None
    if target_ips is None:
        targets = None
    else:
        targets = set(target_ips)
        if not targets:
            return {}

    fn = browser_fn if browser_fn is not None else _real_browse
    try:
        records = await fn(timeout)
    except Exception as exc:
        # mDNS is an optional enrichment; a failure here must not abort
        # the whole scan. Log the reason to stderr so the operator can
        # investigate ("discovered no hosts" has many causes) instead of
        # silently returning an empty map. Wave-3 finding C-020.
        import sys
        print(
            f"mDNS discovery failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return {}

    out: dict[str, str] = {}
    for rec in records:
        if targets is not None and rec.ip not in targets:
            continue
        if rec.ip in out:
            continue
        out[rec.ip] = rec.name
    return out
