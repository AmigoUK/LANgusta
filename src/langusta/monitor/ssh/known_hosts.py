"""Per-user SSH known_hosts store for monitor `ssh_command` checks.

Implements Trust-On-First-use (TOFU): the first time LANgusta connects to
a given `host:port`, the server's host key is recorded in
`~/.langusta/known_hosts`. Subsequent connections verify against the
recorded key and fail if it differs — never auto-accepting a changed key.

File format is OpenSSH-compatible so advanced users can edit it with a
text editor or prepopulate it from their own `~/.ssh/known_hosts`.

Audit S-7: the ``add`` method uses an exclusive file lock (``flock``) to
prevent TOCTOU races where two concurrent first-use checks to the same
host both see ``not contains`` and both append.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HostKeyEntry:
    host: str
    port: int
    key_type: str     # e.g. "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"
    key_b64: str

    def to_openssh_line(self) -> str:
        # Non-default ports use the `[host]:port` bracket syntax per
        # `man 8 sshd` / OpenSSH known_hosts format.
        host_spec = self.host if self.port == 22 else f"[{self.host}]:{self.port}"
        return f"{host_spec} {self.key_type} {self.key_b64}\n"


def _parse_host_spec(raw: str) -> tuple[str, int] | None:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith("[") and "]:" in raw:
        host, _, port_str = raw[1:].partition("]:")
        try:
            return host, int(port_str)
        except ValueError:
            return None
    return raw, 22


def _parse_line(line: str) -> HostKeyEntry | None:
    parts = line.strip().split()
    if len(parts) < 3:
        return None
    host_spec_raw, key_type, key_b64 = parts[0], parts[1], parts[2]
    parsed = _parse_host_spec(host_spec_raw)
    if parsed is None:
        return None
    host, port = parsed
    return HostKeyEntry(host=host, port=port, key_type=key_type, key_b64=key_b64)


class KnownHostsStore:
    """File-backed TOFU store for SSH host keys."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self._path.exists()

    def entries(self) -> list[HostKeyEntry]:
        if not self._path.exists():
            return []
        out: list[HostKeyEntry] = []
        for raw_line in self._path.read_text(encoding="utf-8").splitlines():
            entry = _parse_line(raw_line)
            if entry is not None:
                out.append(entry)
        return out

    def get(self, host: str, port: int) -> HostKeyEntry | None:
        for entry in self.entries():
            if entry.host == host and entry.port == port:
                return entry
        return None

    def contains(self, host: str, port: int) -> bool:
        return self.get(host, port) is not None

    def add(self, entry: HostKeyEntry) -> None:
        """Append a new entry. Refuses to overwrite an existing host:port.

        Uses an exclusive file lock to prevent TOCTOU races where two
        concurrent first-use checks to the same host both pass the
        ``contains`` check and both append (audit S-7).
        """
        self._ensure_parent()
        # Open in a+ so the file is created if it doesn't exist.
        with self._path.open("a+", encoding="utf-8") as f:
            # Exclusive lock for the duration of the check-and-add.
            # LOCK_EX blocks until the lock is acquired.
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                existing_lines = f.read().splitlines()
                for line in existing_lines:
                    parsed = _parse_line(line)
                    if (
                        parsed is not None
                        and parsed.host == entry.host
                        and parsed.port == entry.port
                    ):
                        raise KeyMismatchError(
                            f"{entry.host}:{entry.port} already has a recorded host key; "
                            "remove it from ~/.langusta/known_hosts before re-pinning."
                        )
                f.seek(0, os.SEEK_END)
                f.write(entry.to_openssh_line())
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        # Force 0600 regardless of the process umask — the file carries
        # TOFU host-key pins that a local attacker could overwrite or
        # pre-seed to bypass verification on the first connect.
        # FileNotFoundError means we raced with a cleanup; the next
        # write will re-chmod anyway.
        with contextlib.suppress(FileNotFoundError):
            os.chmod(self._path, 0o600)

    def verify(self, host: str, port: int, key_type: str, key_b64: str) -> None:
        """Raise KeyMismatchError if the presented key doesn't match the pin."""
        recorded = self.get(host, port)
        if recorded is None:
            raise KeyNotPinnedError(f"no recorded key for {host}:{port}")
        if recorded.key_type != key_type or recorded.key_b64 != key_b64:
            raise KeyMismatchError(
                f"host key for {host}:{port} changed "
                f"(pinned {recorded.key_type}, got {key_type})"
            )


class KeyNotPinnedError(LookupError):
    """No host key is recorded for the given host:port."""


class KeyMismatchError(ValueError):
    """Presented host key does not match the recorded pin."""
