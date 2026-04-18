"""SNMP client Protocol.

ADR-0003: every scan path depends on this Protocol, not on pysnmp
internals. Two backends live behind it: `TranscriptBackend` (tests) and
`PysnmpBackend` (real). A `NetSnmpSubprocessBackend` is a reserved seam.

Contract: unreachable hosts and timeouts return `None` — never raise. A
misbehaving SNMP stack should never fail the scan.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from langusta.scan.snmp.auth import SnmpAuth

SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"


@runtime_checkable
class SnmpClient(Protocol):
    """Minimum SNMP surface area the scanner and monitor need.

    `get_sys_descr` is a thin wrapper around `get` for the canonical
    sysDescr OID; it is kept separately so existing scan callers don't
    need to spell the OID out. `get` exists for the generic SNMP-OID
    monitor check kind and for future enrichment passes.

    Both methods accept an `SnmpAuth` so v2c (community) and v3 (USM
    auth+priv) share the same surface.
    """

    async def get_sys_descr(
        self,
        ip: str,
        *,
        auth: SnmpAuth,
        timeout: float = 2.0,
    ) -> str | None:
        """Return the sysDescr string or None if unreachable/timeout/error."""
        ...

    async def get(
        self,
        ip: str,
        oid: str,
        *,
        auth: SnmpAuth,
        timeout: float = 2.0,
    ) -> str | None:
        """Return the string value for `oid`, or None if no response."""
        ...
