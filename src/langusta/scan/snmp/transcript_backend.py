"""Transcript-replay SNMP backend for tests.

Loads a JSON file mapping IP → recorded responses; returns them from
`get_sys_descr` (and `get`). The sentinel `"__TIMEOUT__"` simulates a
timeout.

Transcript shape — top-level is `{ip: record}`. Each record may carry:

    {
      "sys_descr": "<string | __TIMEOUT__ | null>",   # legacy shape
      "oids": { "<numeric_oid>": "<string | __TIMEOUT__ | null>" }
    }

`sys_descr` is equivalent to `oids["1.3.6.1.2.1.1.1.0"]`. Transcripts
ignore the `auth` argument — they model the network response, not the
authentication outcome.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langusta.scan.snmp.auth import SnmpAuth
from langusta.scan.snmp.client import SYS_DESCR_OID

_TIMEOUT_SENTINEL = "__TIMEOUT__"


class TranscriptBackend:
    def __init__(self, transcript: dict[str, dict[str, object]]) -> None:
        self._transcript = transcript

    @classmethod
    def from_path(cls, path: Path) -> TranscriptBackend:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, object]]) -> TranscriptBackend:
        return cls(dict(data))

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
        record = self._transcript.get(ip)
        if record is None:
            return None
        value = _lookup(record, oid)
        if value == _TIMEOUT_SENTINEL:
            await asyncio.sleep(timeout + 0.01)  # would time out in real code
            return None
        if value is None:
            return None
        return str(value)


def _lookup(record: dict[str, object], oid: str) -> object | None:
    oids = record.get("oids")
    if isinstance(oids, dict) and oid in oids:
        return oids[oid]
    # Legacy shape: sys_descr shortcut for the canonical OID.
    if oid == SYS_DESCR_OID and "sys_descr" in record:
        return record["sys_descr"]
    return None
