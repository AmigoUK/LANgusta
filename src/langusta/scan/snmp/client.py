"""SNMP client Protocol.

ADR-0003: every scan path depends on this Protocol, not on pysnmp
internals. Two backends live behind it: `TranscriptBackend` (tests) and
`PysnmpBackend` (real). A `NetSnmpSubprocessBackend` is a reserved seam.

Contract: unreachable hosts and timeouts return `None` — never raise. A
misbehaving SNMP stack should never fail the scan.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SnmpClient(Protocol):
    """Minimum SNMP surface area the orchestrator needs for M5.

    M1 of the SNMP integration = fetch sysDescr (1.3.6.1.2.1.1.1.0) to
    populate detected_os. Later milestones extend with ifTable, LLDP, etc.
    """

    async def get_sys_descr(
        self,
        ip: str,
        *,
        community: str,
        timeout: float = 2.0,
    ) -> str | None:
        """Return the sysDescr string or None if unreachable/timeout/error."""
        ...
