"""Real SNMP backend via pysnmp-lextudio (ADR-0003).

Thin async wrapper over pysnmp's v3arch.asyncio.get_cmd. Any error or
timeout is swallowed and returns None per the Protocol contract; the
scan never fails because a host didn't answer SNMP.

Handles both v2c (community) and v3 authPriv (USM user with auth+priv
protocols). The `auth` dispatch happens in `_build_authdata`; nothing
else knows about pysnmp types.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from langusta.scan.snmp.auth import SnmpAuth, SnmpV2cAuth
from langusta.scan.snmp.client import SYS_DESCR_OID


def _build_authdata(auth: SnmpAuth) -> Any:
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        UsmUserData,
        usm3DESEDEPrivProtocol,
        usmAesCfb128Protocol,
        usmAesCfb192Protocol,
        usmAesCfb256Protocol,
        usmDESPrivProtocol,
        usmHMAC128SHA224AuthProtocol,
        usmHMAC192SHA256AuthProtocol,
        usmHMAC256SHA384AuthProtocol,
        usmHMAC384SHA512AuthProtocol,
        usmHMACMD5AuthProtocol,
        usmHMACSHAAuthProtocol,
        usmNoAuthProtocol,
        usmNoPrivProtocol,
    )

    if isinstance(auth, SnmpV2cAuth):
        return CommunityData(auth.community, mpModel=1)

    auth_map = {
        "NONE": usmNoAuthProtocol,
        "MD5": usmHMACMD5AuthProtocol,
        "SHA": usmHMACSHAAuthProtocol,
        "SHA-224": usmHMAC128SHA224AuthProtocol,
        "SHA-256": usmHMAC192SHA256AuthProtocol,
        "SHA-384": usmHMAC256SHA384AuthProtocol,
        "SHA-512": usmHMAC384SHA512AuthProtocol,
    }
    priv_map = {
        "NONE": usmNoPrivProtocol,
        "DES": usmDESPrivProtocol,
        "3DES": usm3DESEDEPrivProtocol,
        "AES-128": usmAesCfb128Protocol,
        "AES-192": usmAesCfb192Protocol,
        "AES-256": usmAesCfb256Protocol,
    }
    return UsmUserData(
        auth.username,
        authKey=auth.auth_passphrase,
        privKey=auth.priv_passphrase,
        authProtocol=auth_map[auth.auth_protocol],
        privProtocol=priv_map[auth.priv_protocol],
    )


class PysnmpBackend:
    """pysnmp-lextudio v6+ backend supporting SNMP v2c and v3 authPriv."""

    async def get_sys_descr(
        self,
        ip: str,
        *,
        auth: SnmpAuth,
        timeout: float = 2.0,
    ) -> str | None:
        return await self.get(ip, SYS_DESCR_OID, auth=auth, timeout=timeout)

    async def get(
        self,
        ip: str,
        oid: str,
        *,
        auth: SnmpAuth,
        timeout: float = 2.0,
    ) -> str | None:
        try:
            # Imports are local so the scan suite stays fast when SNMP isn't used.
            from pysnmp.hlapi.v3arch.asyncio import (
                ContextData,
                ObjectIdentity,
                ObjectType,
                SnmpEngine,
                UdpTransportTarget,
                get_cmd,
            )
        except ImportError:
            return None

        try:
            authdata = _build_authdata(auth)
        except Exception:
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
                        authdata,
                        transport,
                        ContextData(),
                        ObjectType(ObjectIdentity(oid)),
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
