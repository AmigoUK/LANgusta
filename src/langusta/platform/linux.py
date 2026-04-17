"""Linux `PlatformBackend` — uses `ip neigh` for ARP."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from langusta.platform.base import ArpEntry, InstallRecipe


class LinuxBackend:
    def arp_table(self) -> Iterable[ArpEntry]:
        try:
            out = subprocess.run(
                ["ip", "neigh", "show"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
        except (subprocess.SubprocessError, FileNotFoundError):
            return iter(())
        return self._parse_ip_neigh(out)

    @staticmethod
    def _parse_ip_neigh(output: str) -> Iterable[ArpEntry]:
        """Parse `ip neigh show` output.

        Format per line: `<ip> dev <iface> [lladdr <mac>] <STATE>`.
        Skip FAILED / INCOMPLETE entries (no MAC) and IPv6 link-local entries
        (scanner only cares about IPv4 in v1).
        """
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            ip = parts[0]
            if ":" in ip:  # IPv6 — skip in v1
                continue
            if "lladdr" not in parts:
                continue
            lladdr_idx = parts.index("lladdr")
            if lladdr_idx + 1 >= len(parts):
                continue
            state = parts[-1]
            if state in {"FAILED", "INCOMPLETE"}:
                continue
            yield ip, parts[lladdr_idx + 1]

    def enforce_private(self, path: Path) -> None:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)

    def daemon_install_recipe(self, *, exec_path: str) -> InstallRecipe:
        """Render a systemd user unit for the monitor daemon."""
        home = Path(os.path.expanduser("~"))
        install_path = home / ".config" / "systemd" / "user" / "langusta-monitor.service"
        content = _SYSTEMD_UNIT_TEMPLATE.format(exec_path=exec_path)
        start_hint = (
            "systemctl --user daemon-reload && "
            "systemctl --user enable --now langusta-monitor.service"
        )
        return InstallRecipe(
            manager="systemd-user",
            install_path=install_path,
            content=content,
            start_hint=start_hint,
        )


_SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=LANgusta monitor daemon
Documentation=https://github.com/AmigoUK/LANgusta
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_path} monitor daemon --foreground
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
