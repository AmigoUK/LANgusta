"""AsyncsshBackend TOFU round-trip.

Wave-3 TEST-M-005 (finding M-005): the existing KnownHostsStore tests
prove the store itself works, but there was no end-to-end test that the
SSH backend actually wires it up correctly — first-use records, and
*second-use passes the known_hosts path to asyncssh* rather than leaving
verification disabled.

The asyncssh library is not installed in the test environment; we stub
the module with a minimal fake before importing the backend and
monkey-patch `asyncssh.connect` to capture the kwargs.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from langusta.monitor.ssh.auth import SshPasswordAuth


def _install_asyncssh_stub(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a minimal asyncssh stub into sys.modules so the backend's
    lazy `import asyncssh` succeeds. Returns the stub module so the test
    can set `.connect` on it."""
    stub = types.ModuleType("asyncssh")

    def _not_configured(**_: Any) -> None:
        raise AssertionError("test forgot to set asyncssh.connect")

    stub.connect = _not_configured  # type: ignore[attr-defined]
    stub.import_private_key = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "asyncssh", stub)
    return stub


class _FakeServerKey:
    """Stand-in for an asyncssh SSHKey."""

    def export_public_key(self, format_name: str = "openssh") -> bytes:
        return b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAfaketestkey"


class _FakeConn:
    """Stand-in for an asyncssh SSHClientConnection context."""

    def __init__(self, server_key: object = _FakeServerKey()) -> None:
        self._server_key = server_key

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    def get_server_host_key(self) -> object:
        return self._server_key

    async def run(self, command: str, *, check: bool = False) -> Any:
        class _Proc:
            exit_status = 0
            stdout = ""
            stderr = ""

        return _Proc()


@pytest.mark.asyncio
async def test_tofu_first_use_records_second_use_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing assert: the second connection to the same host:port
    must pass `known_hosts=<path>` to asyncssh, not `known_hosts=None`.
    `None` disables verification permanently; the whole point of TOFU is
    that the second hop is verified."""
    stub = _install_asyncssh_stub(monkeypatch)

    from langusta.monitor.ssh.asyncssh_backend import AsyncsshBackend

    captured: list[dict[str, Any]] = []

    def fake_connect(**kwargs: Any) -> _FakeConn:
        captured.append(dict(kwargs))
        return _FakeConn()

    stub.connect = fake_connect  # type: ignore[attr-defined]

    known_hosts = tmp_path / "known_hosts"
    backend = AsyncsshBackend(known_hosts_path=known_hosts)
    auth = SshPasswordAuth(password="p")

    # First connection — TOFU by design; known_hosts=None is correct here.
    await backend.run_command(
        "10.1.2.3", port=22, username="u", auth=auth,
        command="echo", timeout=5.0,
    )
    # Second connection — MUST pass the pinned path.
    await backend.run_command(
        "10.1.2.3", port=22, username="u", auth=auth,
        command="echo", timeout=5.0,
    )

    assert len(captured) == 2, f"expected 2 connect() calls, got {len(captured)}"
    assert captured[0]["known_hosts"] is None, "first call is TOFU-by-design"
    assert captured[1]["known_hosts"] == str(known_hosts), (
        "second call must pass known_hosts=<path> — None would leave "
        "verification permanently disabled, defeating the pin."
    )


@pytest.mark.asyncio
async def test_tofu_mismatched_key_on_pinned_host_is_not_silently_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a host is already pinned and asyncssh reports a host-key mismatch,
    the command must surface a failure — not silently succeed."""
    stub = _install_asyncssh_stub(monkeypatch)

    from langusta.monitor.ssh.asyncssh_backend import AsyncsshBackend
    from langusta.monitor.ssh.known_hosts import HostKeyEntry, KnownHostsStore

    known_hosts = tmp_path / "known_hosts"
    store = KnownHostsStore(known_hosts)
    store.add(
        HostKeyEntry(
            "10.1.2.3", 22, "ssh-ed25519",
            "AAAAC3NzaC1lZDI1NTE5AAAAoriginalpinnedkey",
        )
    )

    def mismatch_connect(**_: Any) -> _FakeConn:
        # asyncssh raises on a host-key mismatch when known_hosts is set;
        # the backend's except-Exception must convert that to a fail result.
        raise RuntimeError("host key for 10.1.2.3 did not match known_hosts")

    stub.connect = mismatch_connect  # type: ignore[attr-defined]

    backend = AsyncsshBackend(known_hosts_path=known_hosts)
    result = await backend.run_command(
        "10.1.2.3", port=22, username="u",
        auth=SshPasswordAuth(password="p"),
        command="echo", timeout=5.0,
    )

    assert result.exit_code != 0
    assert "host key" in (result.stderr or "").lower()
