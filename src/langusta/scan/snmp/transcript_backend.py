"""Transcript-replay SNMP backend for tests.

Loads a JSON file mapping IP → recorded responses; returns them from
`get_sys_descr`. The sentinel `"__TIMEOUT__"` simulates a timeout.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

_TIMEOUT_SENTINEL = "__TIMEOUT__"


class TranscriptBackend:
    def __init__(self, transcript: dict[str, dict[str, str | None]]) -> None:
        self._transcript = transcript

    @classmethod
    def from_path(cls, path: Path) -> TranscriptBackend:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, data: dict[str, dict[str, str | None]]) -> TranscriptBackend:
        return cls(dict(data))

    async def get_sys_descr(
        self,
        ip: str,
        *,
        community: str,
        timeout: float = 2.0,
    ) -> str | None:
        record = self._transcript.get(ip)
        if record is None:
            return None
        value = record.get("sys_descr")
        if value == _TIMEOUT_SENTINEL:
            await asyncio.sleep(timeout + 0.01)  # would time out in real code
            return None
        return value
