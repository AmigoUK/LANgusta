"""AsyncsshBackend TOFU round-trip.

Wave-3 TEST-M-005 (finding M-005): the existing KnownHostsStore tests
prove the store itself works, but there was no end-to-end test that the
SSH backend actually wires it up correctly — first-use records, and
*second-use passes the known_hosts path to asyncssh* rather than leaving
verification disabled.

Audit S-3: TOFU first-use now splits into a credential-free key-record
phase (via ``create_connection`` + ``SSHClient`` callback) and an auth
phase (``connect`` with pinned known_hosts). Tests verify no credentials
are passed during the key-record phase.

The asyncssh library is not installed in the test environment; we stub
the module with a minimal fake before importing the backend and
monkey-patch `asyncssh.connect` / `asyncssh.create_connection` to capture
the kwargs.
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
    lazy `import asyncssh` succeeds."""
    stub = types.ModuleType("asyncssh")

    def _not_configured(**_: Any) -> None:
        raise AssertionError("test forgot to set asyncssh.connect")

    stub.connect = _not_configured  # type: ignore[attr-defined]
    stub.create_connection = _not_configured  # type: ignore[attr-defined]
    stub.import_private_key = lambda *a, **k: None  # type: ignore[attr-defined]
    stub.DISC_BY_APPLICATION = 11  # type: ignore[attr-defined]
    stub.SSHClient = object  # type: ignore[attr-defined]
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


class _FakeKeyExchangeConn:
    """Stand-in for the connection returned by ``create_connection``
    during the TOFU key-record phase."""

    def __init__(self, server_key: object = _FakeServerKey()) -> None:
        self._server_key = server_key

    def get_server_host_key(self) -> object:
        return self._server_key

    def disconnect(self, *_: object) -> None:
        pass


@pytest.mark.asyncio
async def test_tofu_first_use_records_second_use_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing assert: the second connection to the same host:port
    must pass `known_hosts=<path>` to asyncssh, not `known_hosts=None`.
    The first-use key-record phase must use ``create_connection`` with
    ``agent_path=None`` and NO credentials (audit S-3)."""
    stub = _install_asyncssh_stub(monkeypatch)

    from langusta.monitor.ssh.asyncssh_backend import AsyncsshBackend

    connect_calls: list[dict[str, Any]] = []
    create_calls: list[dict[str, Any]] = []

    def fake_connect(**kwargs: Any) -> _FakeConn:
        connect_calls.append(dict(kwargs))
        return _FakeConn()

    async def fake_create_connection(client_factory, host, **kwargs):
        create_calls.append({"host": host, **kwargs})
        client = client_factory()
        conn = _FakeKeyExchangeConn()
        # Simulate asyncssh calling connection_made after key exchange.
        client.connection_made(conn)
        return conn

    stub.connect = fake_connect  # type: ignore[attr-defined]
    stub.create_connection = fake_create_connection  # type: ignore[attr-defined]

    known_hosts = tmp_path / "known_hosts"
    backend = AsyncsshBackend(known_hosts_path=known_hosts)
    auth = SshPasswordAuth(password="secret-password")

    # First connection — TOFU: key-record phase then auth phase.
    await backend.run_command(
        "10.1.2.3", port=22, username="u", auth=auth,
        command="echo", timeout=5.0,
    )

    # Key-record phase must not carry credentials.
    assert len(create_calls) == 1, (
        f"expected 1 create_connection call (key-record), got {len(create_calls)}"
    )
    kc = create_calls[0]
    assert kc.get("agent_path") is None, "agent must be disabled during key-record"
    assert "password" not in kc, "password must NOT be sent during key-record phase"
    assert "client_keys" not in kc, "keys must NOT be sent during key-record phase"

    # Auth phase: connect with pinned known_hosts (not None).
    assert len(connect_calls) == 1, (
        f"expected 1 connect() call after key-record, got {len(connect_calls)}"
    )
    assert connect_calls[0]["known_hosts"] == str(known_hosts), (
        "auth-phase connect must pass known_hosts=<path> after key recording"
    )

    # Second connection — key already pinned; no create_connection needed.
    await backend.run_command(
        "10.1.2.3", port=22, username="u", auth=auth,
        command="echo", timeout=5.0,
    )
    assert len(create_calls) == 1, "second use must NOT call create_connection"
    assert len(connect_calls) == 2
    assert connect_calls[1]["known_hosts"] == str(known_hosts), (
        "second call must pass known_hosts=<path>"
    )


@pytest.mark.asyncio
async def test_tofu_record_failure_surfaces_in_result_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the first-use TOFU key recording fails (e.g. the server doesn't
    present a host key), the command must surface that — not silently
    succeed."""
    stub = _install_asyncssh_stub(monkeypatch)

    from langusta.monitor.ssh.asyncssh_backend import AsyncsshBackend

    async def fake_create_connection(client_factory, host, **kwargs):
        client = client_factory()
        conn = _FakeKeyExchangeConn(server_key=None)
        client.connection_made(conn)
        return conn

    stub.create_connection = fake_create_connection  # type: ignore[attr-defined]
    stub.connect = lambda **kw: _FakeConn()  # type: ignore[attr-defined]

    known_hosts = tmp_path / "known_hosts"
    backend = AsyncsshBackend(known_hosts_path=known_hosts)
    result = await backend.run_command(
        "10.1.2.3", port=22, username="u",
        auth=SshPasswordAuth(password="p"),
        command="echo", timeout=5.0,
    )

    assert result.exit_code != 0, (
        "TOFU record failure silently returned the command's exit code"
    )
    stderr = (result.stderr or "").lower()
    assert "tofu" in stderr or "host key" in stderr, (
        f"stderr should name the TOFU failure; got {result.stderr!r}"
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
