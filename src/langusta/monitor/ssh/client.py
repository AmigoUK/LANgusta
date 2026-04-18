"""SSH client Protocol + result type.

Contract matches the SnmpClient's discipline: `run_command` never raises.
Auth failures, connection refusals, and timeouts all surface as an
`SshResult` with exit_code = -1 and the error summary in `stderr`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from langusta.monitor.ssh.auth import SshAuth


@dataclass(frozen=True, slots=True)
class SshResult:
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: float | None


@runtime_checkable
class SshClient(Protocol):
    async def run_command(
        self,
        host: str,
        *,
        port: int,
        username: str,
        auth: SshAuth,
        command: str,
        timeout: float,
    ) -> SshResult:
        """Execute `command` on `host:port` as `username`."""
        ...
