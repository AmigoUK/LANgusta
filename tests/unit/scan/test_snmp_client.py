"""SNMP client Protocol tests.

The Protocol defines the minimum surface the scanner and monitor need.
Tests exercise the TranscriptBackend (fixture replay); the PysnmpBackend
gets smoke-tested in an opt-in integration test against a containerised
snmpd.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langusta.scan.snmp.auth import SnmpV2cAuth, SnmpV3Auth
from langusta.scan.snmp.client import SYS_DESCR_OID, SnmpClient
from langusta.scan.snmp.transcript_backend import TranscriptBackend

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "snmp_transcripts" / "sample.json"

V2C = SnmpV2cAuth(community="public")


def _transcript() -> TranscriptBackend:
    return TranscriptBackend.from_path(FIXTURE)


def test_transcript_backend_implements_protocol() -> None:
    assert isinstance(_transcript(), SnmpClient)


@pytest.mark.asyncio
async def test_get_sys_descr_returns_recorded_value() -> None:
    be = _transcript()
    assert "Cisco" in (await be.get_sys_descr("10.0.0.1", auth=V2C))


@pytest.mark.asyncio
async def test_get_sys_descr_for_unreachable_returns_none() -> None:
    be = _transcript()
    # Not in the transcript at all.
    assert await be.get_sys_descr("10.0.0.99", auth=V2C) is None


@pytest.mark.asyncio
async def test_get_sys_descr_explicit_null_returns_none() -> None:
    be = _transcript()
    # Host is in the transcript but sys_descr is explicitly null.
    assert await be.get_sys_descr("10.0.0.3", auth=V2C) is None


@pytest.mark.asyncio
async def test_get_sys_descr_timeout_returns_none() -> None:
    """Protocol contract: timeouts surface as None, never exceptions."""
    be = TranscriptBackend.from_dict({"10.0.0.1": {"sys_descr": "__TIMEOUT__"}})
    assert await be.get_sys_descr("10.0.0.1", auth=V2C, timeout=0.01) is None


def test_transcript_backend_from_dict_roundtrip() -> None:
    data = {"1.2.3.4": {"sys_descr": "test"}}
    be = TranscriptBackend.from_dict(data)
    # Serialise again: TranscriptBackend stores the dict.
    assert be._transcript == data  # pyright: ignore[reportPrivateUsage]


def test_transcript_backend_from_path_reads_json(tmp_path: Path) -> None:
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"9.9.9.9": {"sys_descr": "hello"}}))
    be = TranscriptBackend.from_path(f)
    assert be._transcript == {"9.9.9.9": {"sys_descr": "hello"}}  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_arbitrary_oid_via_oids_map() -> None:
    be = TranscriptBackend.from_dict({
        "10.0.0.1": {"oids": {"1.3.6.1.2.1.1.3.0": "12345"}},
    })
    assert await be.get("10.0.0.1", "1.3.6.1.2.1.1.3.0", auth=V2C) == "12345"


@pytest.mark.asyncio
async def test_get_oid_falls_back_to_sys_descr_shortcut() -> None:
    be = TranscriptBackend.from_dict({"10.0.0.1": {"sys_descr": "Linux"}})
    assert await be.get("10.0.0.1", SYS_DESCR_OID, auth=V2C) == "Linux"


@pytest.mark.asyncio
async def test_get_unknown_oid_returns_none() -> None:
    be = TranscriptBackend.from_dict({"10.0.0.1": {"oids": {"1.3.6.1.2.1.1.1.0": "X"}}})
    assert await be.get("10.0.0.1", "1.3.6.1.4.1.42", auth=V2C) is None


@pytest.mark.asyncio
async def test_transcript_accepts_snmp_v3_auth() -> None:
    """Transcripts ignore auth, but the signature must accept SnmpV3Auth."""
    v3 = SnmpV3Auth(
        username="admin",
        auth_protocol="SHA",
        auth_passphrase="authpass123",
        priv_protocol="AES-128",
        priv_passphrase="privpass123",
    )
    be = TranscriptBackend.from_dict({"10.0.0.1": {"sys_descr": "Cisco"}})
    assert await be.get_sys_descr("10.0.0.1", auth=v3) == "Cisco"
