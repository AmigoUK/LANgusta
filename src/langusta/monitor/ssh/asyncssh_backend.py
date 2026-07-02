"""Real SSH backend via asyncssh with TOFU host-key pinning.

On first connection to a given `host:port`, the server's host key is
recorded in `~/.langusta/known_hosts` via a credential-free key-exchange
phase. No password or private key is sent until the host key is pinned,
so an active MITM on first use cannot capture credentials. Subsequent
connections verify against the recorded key — a change raises and fails
the check rather than silently accepting the new key.

Audit S-3: TOFU first-use now splits into a key-record phase (no auth)
and an auth phase (pinned known_hosts).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from langusta import paths
from langusta.monitor.ssh.auth import SshAuth, SshKeyAuth, SshPasswordAuth
from langusta.monitor.ssh.client import SshResult
from langusta.monitor.ssh.known_hosts import HostKeyEntry, KnownHostsStore


class AsyncsshBackend:
    """Production SSH backend. Host-key pinning is TOFU: first-use records,
    later-use verifies. A changed key aborts the command with a clear
    error — LANgusta never auto-accepts a rotated host key.
    """

    def __init__(self, known_hosts_path: Path | None = None) -> None:
        self._store = KnownHostsStore(
            known_hosts_path if known_hosts_path is not None
            else paths.known_hosts_path(),
        )

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
            return SshResult(
                exit_code=-1, stdout="", stderr="asyncssh not installed",
                elapsed_ms=None,
            )

        # TOFU: on first use, record the host key via a credential-free
        # key-exchange phase BEFORE sending any passwords or keys. This
        # prevents a MITM from capturing credentials on the initial
        # connection (audit S-3).
        if not self._store.contains(host, port):
            start = time.monotonic()
            try:
                async with asyncio.timeout(timeout):
                    await self._tofu_record_key(
                        asyncssh, host, port, username,
                    )
            except TimeoutError:
                elapsed = (time.monotonic() - start) * 1000.0
                return SshResult(
                    exit_code=-1, stdout="", stderr="timeout during TOFU key exchange",
                    elapsed_ms=elapsed,
                )
            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000.0
                return SshResult(
                    exit_code=-1, stdout="",
                    stderr=f"TOFU key exchange failed: {exc}",
                    elapsed_ms=elapsed,
                )
            if not self._store.contains(host, port):
                elapsed = (time.monotonic() - start) * 1000.0
                return SshResult(
                    exit_code=-1, stdout="",
                    stderr="TOFU: host key was not recorded",
                    elapsed_ms=elapsed,
                )

        connect_kwargs: dict[str, object] = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": str(self._store.path),
        }

        if isinstance(auth, SshKeyAuth):
            try:
                key = asyncssh.import_private_key(
                    auth.private_key_pem, passphrase=auth.passphrase,
                )
            except Exception as exc:
                return SshResult(
                    exit_code=-1, stdout="", stderr=f"bad key: {exc}",
                    elapsed_ms=None,
                )
            connect_kwargs["client_keys"] = [key]
        elif isinstance(auth, SshPasswordAuth):
            connect_kwargs["password"] = auth.password
        else:  # pragma: no cover — type system should prevent this
            return SshResult(
                exit_code=-1, stdout="", stderr="unknown auth type",
                elapsed_ms=None,
            )

        start = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                async with asyncssh.connect(**connect_kwargs) as conn:
                    proc = await conn.run(command, check=False)
        except TimeoutError:
            elapsed = (time.monotonic() - start) * 1000.0
            return SshResult(
                exit_code=-1, stdout="", stderr="timeout", elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000.0
            return SshResult(
                exit_code=-1, stdout="", stderr=str(exc), elapsed_ms=elapsed,
            )
        elapsed = (time.monotonic() - start) * 1000.0
        stdout = _as_text(proc.stdout)
        stderr = _as_text(proc.stderr)
        exit_code = proc.exit_status if proc.exit_status is not None else -1
        return SshResult(
            exit_code=int(exit_code), stdout=stdout, stderr=stderr,
            elapsed_ms=elapsed,
        )

    async def _tofu_record_key(
        self, asyncssh, host: str, port: int, username: str,
    ) -> None:
        """Phase 1 of TOFU: establish a key-exchange-only connection with
        NO credentials, capture the server host key, and disconnect before
        authentication begins.

        Uses asyncssh's SSHClient callback so the key is captured in
        ``connection_made`` (which fires after key exchange but before
        auth) and the connection is immediately disconnected.

        Raises ``RuntimeError`` if the server did not present a host key.
        """
        captured: list = []

        class _KeyGrabber(asyncssh.SSHClient):
            def connection_made(self, conn):
                key = conn.get_server_host_key()
                if key is not None:
                    captured.append(key)
                conn.disconnect(
                    asyncssh.DISC_BY_APPLICATION, "TOFU key record only",
                )

        with contextlib.suppress(Exception):
            await asyncssh.create_connection(
                _KeyGrabber, host, port=port,
                username=username,
                known_hosts=None,
                agent_path=None,
            )

        if not captured or captured[0] is None:
            raise RuntimeError("server did not present a host key")

        server_key = captured[0]
        openssh_bytes = server_key.export_public_key(format_name="openssh")
        parts = openssh_bytes.decode("utf-8", errors="replace").split()
        if len(parts) < 2:
            raise RuntimeError(
                f"host key export returned unexpected shape: {openssh_bytes!r}"
            )
        entry = HostKeyEntry(
            host=host, port=port, key_type=parts[0], key_b64=parts[1],
        )
        try:
            self._store.add(entry)
        except Exception:
            if not self._store.contains(host, port):
                raise
            return


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)
