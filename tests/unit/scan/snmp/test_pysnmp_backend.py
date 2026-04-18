"""PysnmpBackend unit tests — verify correct pysnmp auth-data construction.

These tests exercise `_build_authdata` against stubbed pysnmp symbols to
assert the right v2c/v3 classes are instantiated with the right params.
They do not open sockets; full integration against a real snmpd is a
separate opt-in suite.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from langusta.scan.snmp.auth import SnmpV2cAuth, SnmpV3Auth


def _install_pysnmp_stub() -> dict[str, Any]:
    """Install a minimal stub for pysnmp.hlapi.v3arch.asyncio and record calls."""
    calls: dict[str, Any] = {"community": [], "usm": []}

    class _Proto:
        def __init__(self, name: str) -> None:
            self.name = name

        def __repr__(self) -> str:
            return f"<Proto {self.name}>"

    class _CommunityData:
        def __init__(self, community: str, **kwargs: object) -> None:
            calls["community"].append((community, kwargs))

    class _UsmUserData:
        def __init__(self, username: str, **kwargs: object) -> None:
            calls["usm"].append((username, kwargs))

    mod = types.ModuleType("pysnmp.hlapi.v3arch.asyncio")
    mod.CommunityData = _CommunityData
    mod.UsmUserData = _UsmUserData
    for name in (
        "usmNoAuthProtocol", "usmHMACMD5AuthProtocol", "usmHMACSHAAuthProtocol",
        "usmHMAC128SHA224AuthProtocol", "usmHMAC192SHA256AuthProtocol",
        "usmHMAC256SHA384AuthProtocol", "usmHMAC384SHA512AuthProtocol",
        "usmNoPrivProtocol", "usmDESPrivProtocol", "usm3DESEDEPrivProtocol",
        "usmAesCfb128Protocol", "usmAesCfb192Protocol", "usmAesCfb256Protocol",
    ):
        setattr(mod, name, _Proto(name))
    # The real import path has the parent packages too.
    sys.modules["pysnmp"] = types.ModuleType("pysnmp")
    sys.modules["pysnmp.hlapi"] = types.ModuleType("pysnmp.hlapi")
    sys.modules["pysnmp.hlapi.v3arch"] = types.ModuleType("pysnmp.hlapi.v3arch")
    sys.modules["pysnmp.hlapi.v3arch.asyncio"] = mod
    return calls


@pytest.fixture
def pysnmp_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # Preserve any previously-imported real pysnmp to restore after.
    saved = {name: sys.modules.get(name) for name in (
        "pysnmp", "pysnmp.hlapi", "pysnmp.hlapi.v3arch", "pysnmp.hlapi.v3arch.asyncio",
    )}
    try:
        yield _install_pysnmp_stub()
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def test_build_authdata_v2c(pysnmp_stub: dict[str, Any]) -> None:
    from langusta.scan.snmp.pysnmp_backend import _build_authdata
    _build_authdata(SnmpV2cAuth(community="public"))
    assert pysnmp_stub["community"] == [("public", {"mpModel": 1})]
    assert pysnmp_stub["usm"] == []


def test_build_authdata_v3_sha_aes128(pysnmp_stub: dict[str, Any]) -> None:
    from langusta.scan.snmp.pysnmp_backend import _build_authdata
    auth = SnmpV3Auth(
        username="admin",
        auth_protocol="SHA",
        auth_passphrase="authpass",
        priv_protocol="AES-128",
        priv_passphrase="privpass",
    )
    _build_authdata(auth)
    assert pysnmp_stub["community"] == []
    assert len(pysnmp_stub["usm"]) == 1
    username, kwargs = pysnmp_stub["usm"][0]
    assert username == "admin"
    assert kwargs["authKey"] == "authpass"
    assert kwargs["privKey"] == "privpass"
    assert kwargs["authProtocol"].name == "usmHMACSHAAuthProtocol"
    assert kwargs["privProtocol"].name == "usmAesCfb128Protocol"


def test_build_authdata_v3_auth_only_no_priv(pysnmp_stub: dict[str, Any]) -> None:
    from langusta.scan.snmp.pysnmp_backend import _build_authdata
    auth = SnmpV3Auth(
        username="admin",
        auth_protocol="SHA-256",
        auth_passphrase="authpass",
        priv_protocol="NONE",
        priv_passphrase=None,
    )
    _build_authdata(auth)
    _, kwargs = pysnmp_stub["usm"][0]
    assert kwargs["authProtocol"].name == "usmHMAC192SHA256AuthProtocol"
    assert kwargs["privProtocol"].name == "usmNoPrivProtocol"
    assert kwargs["privKey"] is None
