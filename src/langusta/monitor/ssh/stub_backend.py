"""Scripted SSH backend for tests — no network."""

from __future__ import annotations

from dataclasses import dataclass

from langusta.monitor.ssh.auth import SshAuth
from langusta.monitor.ssh.client import SshResult


@dataclass(frozen=True, slots=True)
class _Response:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: float | None = 5.0


class StubBackend:
    """Returns pre-scripted responses keyed by `(host, command)`.

    Usage:
        stub = StubBackend({("10.0.0.1", "uptime"): _Response(0, "up 3 days")})
    """

    def __init__(self, responses: dict[tuple[str, str], _Response]) -> None:
        self._responses = responses
        self.calls: list[dict] = []

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
        self.calls.append(
            {"host": host, "port": port, "username": username,
             "command": command, "timeout": timeout},
        )
        response = self._responses.get((host, command))
        if response is None:
            return SshResult(
                exit_code=-1, stdout="", stderr="no stubbed response",
                elapsed_ms=None,
            )
        return SshResult(
            exit_code=response.exit_code,
            stdout=response.stdout,
            stderr=response.stderr,
            elapsed_ms=response.elapsed_ms,
        )


Response = _Response
