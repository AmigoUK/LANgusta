"""Real SNMP backend via pysnmp-lextudio (ADR-0003).

Thin async wrapper over pysnmp's v3arch.asyncio.get_cmd. Any error or
timeout is swallowed and returns None per the Protocol contract; the
scan never fails because a host didn't answer SNMP.

This module intentionally has minimal surface: just `get_sys_descr`.
Fuller SNMP (interface table, LLDP neighbors) lands in later milestones.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

_SYS_DESCR_OID = "1.3.6.1.2.1.1.1.0"


class PysnmpBackend:
    """pysnmp-lextudio v6+ SNMPv2c backend."""

    async def get_sys_descr(
        self,
        ip: str,
        *,
        community: str,
        timeout: float = 2.0,
    ) -> str | None:
        try:
            # Imports are local so the scan suite stays fast when SNMP isn't used.
            from pysnmp.hlapi.v3arch.asyncio import (
                CommunityData,
                ContextData,
                ObjectIdentity,
                ObjectType,
                SnmpEngine,
                UdpTransportTarget,
                get_cmd,
            )
        except ImportError:
            return None

        engine = SnmpEngine()
        try:
            transport = await UdpTransportTarget.create(
                (ip, 161), timeout=timeout, retries=0,
            )
        except Exception:
            with suppress(Exception):
                engine.close_dispatcher()  # type: ignore[attr-defined]
            return None

        try:
            try:
                result = await asyncio.wait_for(
                    get_cmd(
                        engine,
                        CommunityData(community, mpModel=1),
                        transport,
                        ContextData(),
                        ObjectType(ObjectIdentity(_SYS_DESCR_OID)),
                    ),
                    timeout=timeout + 0.5,
                )
            except TimeoutError:
                return None
            except Exception:
                return None
            error_indication, error_status, _error_index, var_binds = result
            if error_indication or error_status:
                return None
            for var_bind in var_binds:
                return str(var_bind[1])
            return None
        finally:
            with suppress(Exception):
                engine.close_dispatcher()  # type: ignore[attr-defined]
