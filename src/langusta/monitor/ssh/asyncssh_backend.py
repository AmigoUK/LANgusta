"""Real SSH backend via asyncssh with TOFU host-key pinning.

On first connection to a given `host:port`, the server's host key is
recorded in `~/.langusta/known_hosts`. Subsequent connections verify
against the recorded key — a change raises and fails the check rather
than silently accepting the new key.
"""

from __future__ import annotations

import asyncio
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

        connect_kwargs: dict[str, object] = {
            "host": host,
            "port": port,
            "username": username,
        }
        if self._store.contains(host, port):
            # Pinned host — let asyncssh verify against our known_hosts file.
            connect_kwargs["known_hosts"] = str(self._store.path)
        else:
            # First use — connect without verification so we can record the
            # key. Subsequent connections will go through the pinned path.
            connect_kwargs["known_hosts"] = None

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
                    if not self._store.contains(host, port):
                        # TOFU failure must surface: if we can't record the
                        # key now, the next cycle won't flip to the pinned
                        # branch and we'd be stuck in first-use-unverified
                        # mode forever. Short-circuit with a clear error
                        # rather than silently returning the command exit.
                        try:
                            self._record_host_key(conn, host, port)
                        except Exception as exc:
                            elapsed = (time.monotonic() - start) * 1000.0
                            return SshResult(
                                exit_code=-1, stdout="",
                                stderr=f"TOFU host-key record failed: {exc}",
                                elapsed_ms=elapsed,
                            )
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

    def _record_host_key(self, conn, host: str, port: int) -> None:
        """Extract the negotiated host key and append to the TOFU store.

        Raises on any step that prevents the key from being recorded so
        the caller can surface TOFU failure in the SshResult. The only
        silent path is a concurrent-writer race against `store.add`,
        which we tolerate because the other writer has already produced
        an identical pin.
        """
        server_key = conn.get_server_host_key()
        if server_key is None:
            raise RuntimeError("asyncssh returned no server host key")
        openssh_bytes = server_key.export_public_key(format_name="openssh")
        parts = openssh_bytes.decode("utf-8", errors="replace").split()
        if len(parts) < 2:
            raise RuntimeError(
                "asyncssh host key export returned unexpected shape: "
                f"{openssh_bytes!r}"
            )
        key_type, key_b64 = parts[0], parts[1]
        entry = HostKeyEntry(
            host=host, port=port, key_type=key_type, key_b64=key_b64,
        )
        try:
            self._store.add(entry)
        except Exception:
            # Race with a concurrent monitor cycle — the other writer
            # wins; verification against the just-written entry happens
            # next run. We only tolerate this case; genuine add failures
            # (e.g. disk full, permission denied) still raise up through
            # the caller below.
            if not self._store.contains(host, port):
                raise
            return


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)
