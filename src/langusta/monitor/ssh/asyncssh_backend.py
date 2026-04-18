"""Real SSH backend via asyncssh.

Known_hosts verification is disabled (known_hosts=None) for v1 to match
user expectations when first onboarding assets. A dedicated host-key
pinning feature is on the post-v0.2.0 backlog.
"""

from __future__ import annotations

import asyncio
import time

from langusta.monitor.ssh.auth import SshAuth, SshKeyAuth, SshPasswordAuth
from langusta.monitor.ssh.client import SshResult


class AsyncsshBackend:
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
        try:
            import asyncssh
        except ImportError:
            return SshResult(exit_code=-1, stdout="", stderr="asyncssh not installed", elapsed_ms=None)

        connect_kwargs: dict[str, object] = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,  # v1: host-key pinning deferred
        }
        if isinstance(auth, SshKeyAuth):
            try:
                key = asyncssh.import_private_key(
                    auth.private_key_pem, passphrase=auth.passphrase,
                )
            except Exception as exc:
                return SshResult(exit_code=-1, stdout="", stderr=f"bad key: {exc}", elapsed_ms=None)
            connect_kwargs["client_keys"] = [key]
        elif isinstance(auth, SshPasswordAuth):
            connect_kwargs["password"] = auth.password
        else:  # pragma: no cover — type system should prevent this
            return SshResult(exit_code=-1, stdout="", stderr="unknown auth type", elapsed_ms=None)

        start = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                async with asyncssh.connect(**connect_kwargs) as conn:
                    proc = await conn.run(command, check=False)
        except TimeoutError:
            elapsed = (time.monotonic() - start) * 1000.0
            return SshResult(exit_code=-1, stdout="", stderr="timeout", elapsed_ms=elapsed)
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            return SshResult(exit_code=-1, stdout="", stderr=str(exc), elapsed_ms=elapsed)
        elapsed = (time.monotonic() - start) * 1000.0
        stdout = _as_text(proc.stdout)
        stderr = _as_text(proc.stderr)
        exit_code = proc.exit_status if proc.exit_status is not None else -1
        return SshResult(
            exit_code=int(exit_code), stdout=stdout, stderr=stderr, elapsed_ms=elapsed,
        )


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)
