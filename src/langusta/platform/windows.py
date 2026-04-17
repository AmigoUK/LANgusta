"""Windows stub `PlatformBackend`.

Per ADR-0004, native Win32 is not supported in v1. Users run LANgusta under
WSL2. This stub exists so callers get a clear, typed error rather than a
silent no-op or cryptic traceback.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from langusta.platform.base import ArpEntry, NotImplementedCapability

_WSL2_HINT = (
    "Windows native is not supported in LANgusta v1; please run under WSL2. "
    "See docs/adr/0004-platform-support.md."
)


class WindowsStubBackend:
    def arp_table(self) -> Iterable[ArpEntry]:
        raise NotImplementedCapability(_WSL2_HINT)

    def enforce_private(self, path: Path) -> None:
        raise NotImplementedCapability(_WSL2_HINT)
