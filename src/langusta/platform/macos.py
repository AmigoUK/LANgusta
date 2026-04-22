"""macOS `PlatformBackend` — uses `arp -a` for ARP."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path

from langusta.platform.base import ArpEntry, InstallRecipe

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

    def daemon_install_recipe(self, *, exec_path: str) -> InstallRecipe:
        """Render a launchd user-agent plist for the monitor daemon."""
        home = Path(os.path.expanduser("~"))
        label = "uk.attv.langusta.monitor"
        install_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
        # Logs under ~/Library/Logs (per-user, user-owned). /tmp is world-
        # readable + world-writable on macOS and invites tail-everything
        # eavesdropping plus symlink-attack at pre-create.
        stdout_log = home / "Library" / "Logs" / "langusta-monitor.out.log"
        stderr_log = home / "Library" / "Logs" / "langusta-monitor.err.log"
        content = _LAUNCHD_PLIST_TEMPLATE.format(
            label=label,
            exec_path=exec_path,
            stdout_path=stdout_log,
            stderr_path=stderr_log,
        )
        start_hint = f"launchctl load {install_path}"
        return InstallRecipe(
            manager="launchd",
            install_path=install_path,
            content=content,
            start_hint=start_hint,
        )


_LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exec_path}</string>
        <string>monitor</string>
        <string>daemon</string>
        <string>--foreground</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{stdout_path}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_path}</string>
</dict>
</plist>
"""
