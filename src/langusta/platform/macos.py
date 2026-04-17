"""macOS `PlatformBackend` — uses `arp -a` for ARP."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from langusta.platform.base import ArpEntry

# Example:
#   router.local (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]
#   ? (192.168.1.99) at (incomplete) on en0 ifscope [ethernet]
_ARP_A_RE = re.compile(
    r"\((?P<ip>\d{1,3}(?:\.\d{1,3}){3})\)\s+at\s+(?P<mac>[0-9a-fA-F:]{11,17})"
)


class MacOSBackend:
    def arp_table(self) -> Iterable[ArpEntry]:
        try:
            out = subprocess.run(
                ["arp", "-a"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return iter(())
        return self._parse_arp_a(out)

    @staticmethod
    def _parse_arp_a(output: str) -> Iterable[ArpEntry]:
        seen: set[tuple[str, str]] = set()
        for line in output.splitlines():
            match = _ARP_A_RE.search(line)
            if not match:
                continue
            ip, mac = match.group("ip"), match.group("mac").lower()
            # "(incomplete)" lacks a mac that matches [0-9a-f:] — already filtered.
            key = (ip, mac)
            if key in seen:
                continue
            seen.add(key)
            yield ip, mac

    def enforce_private(self, path: Path) -> None:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
