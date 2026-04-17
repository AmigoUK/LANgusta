"""SNMP client Protocol tests.

The Protocol defines the minimum surface the scanner needs. Tests exercise
the TranscriptBackend (fixture replay); the PysnmpBackend gets smoke-
tested in an opt-in integration test against a containerised snmpd.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langusta.scan.snmp.client import SnmpClient
from langusta.scan.snmp.transcript_backend import TranscriptBackend

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "snmp_transcripts" / "sample.json"


def _transcript() -> TranscriptBackend:
    return TranscriptBackend.from_path(FIXTURE)


def test_transcript_backend_implements_protocol() -> None:
    assert isinstance(_transcript(), SnmpClient)


@pytest.mark.asyncio
async def test_get_sys_descr_returns_recorded_value() -> None:
    be = _transcript()
    assert "Cisco" in (await be.get_sys_descr("10.0.0.1", community="public"))


@pytest.mark.asyncio
async def test_get_sys_descr_for_unreachable_returns_none() -> None:
    be = _transcript()
    # Not in the transcript at all.
    assert await be.get_sys_descr("10.0.0.99", community="public") is None


@pytest.mark.asyncio
async def test_get_sys_descr_explicit_null_returns_none() -> None:
    be = _transcript()
    # Host is in the transcript but sys_descr is explicitly null.
    assert await be.get_sys_descr("10.0.0.3", community="public") is None


@pytest.mark.asyncio
async def test_get_sys_descr_timeout_returns_none() -> None:
    """Protocol contract: timeouts surface as None, never exceptions."""
    be = TranscriptBackend.from_dict({"10.0.0.1": {"sys_descr": "__TIMEOUT__"}})
    assert await be.get_sys_descr("10.0.0.1", community="public", timeout=0.01) is None


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
