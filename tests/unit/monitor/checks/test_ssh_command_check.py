"""SshCommandCheck unit tests — evaluation logic only (stub client)."""

from __future__ import annotations

import pytest

from langusta.monitor.checks.ssh_command import SshCommandCheck
from langusta.monitor.ssh.auth import SshPasswordAuth
from langusta.monitor.ssh.stub_backend import Response, StubBackend

AUTH = SshPasswordAuth(password="hunter2")


def _stub(host: str, command: str, response: Response) -> StubBackend:
    return StubBackend({(host, command): response})


@pytest.mark.asyncio
async def test_exit_zero_is_ok() -> None:
    stub = _stub("10.0.0.1", "uptime", Response(exit_code=0, stdout="up 3 days", elapsed_ms=5.0))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="uptime", username="root",
        ssh_auth=AUTH, ssh_client=stub,
    )
    assert result.status == "ok"
    assert result.latency_ms == 5.0
    assert "up 3 days" in (result.detail or "")


@pytest.mark.asyncio
async def test_nonzero_exit_is_fail() -> None:
    stub = _stub("10.0.0.1", "cat /nope", Response(exit_code=1, stderr="No such file", elapsed_ms=3.0))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="cat /nope", username="root",
        ssh_auth=AUTH, ssh_client=stub,
    )
    assert result.status == "fail"
    assert "exit 1" in (result.detail or "")
    assert "No such file" in (result.detail or "")


@pytest.mark.asyncio
async def test_custom_success_exit_code() -> None:
    stub = _stub("10.0.0.1", "check", Response(exit_code=42))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="check", username="root",
        ssh_auth=AUTH, ssh_client=stub, success_exit_code=42,
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_stdout_pattern_matches() -> None:
    stub = _stub("10.0.0.1", "df -h /", Response(exit_code=0, stdout="Filesystem  45% /"))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="df -h /", username="root",
        ssh_auth=AUTH, ssh_client=stub, stdout_pattern=r"[0-9]+%",
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_stdout_pattern_mismatch_is_fail() -> None:
    stub = _stub("10.0.0.1", "whoami", Response(exit_code=0, stdout="nobody"))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="whoami", username="root",
        ssh_auth=AUTH, ssh_client=stub, stdout_pattern=r"^root$",
    )
    assert result.status == "fail"
    assert "did not match" in (result.detail or "")


@pytest.mark.asyncio
async def test_bad_regex_is_fail() -> None:
    stub = _stub("10.0.0.1", "echo hi", Response(exit_code=0, stdout="hi"))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="echo hi", username="root",
        ssh_auth=AUTH, ssh_client=stub, stdout_pattern="(",
    )
    assert result.status == "fail"
    assert "bad stdout_pattern" in (result.detail or "")


@pytest.mark.asyncio
async def test_missing_auth() -> None:
    stub = _stub("10.0.0.1", "echo", Response(exit_code=0))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="echo", username="root",
        ssh_auth=None, ssh_client=stub,
    )
    assert result.status == "fail"
    assert "ssh_auth" in (result.detail or "")


@pytest.mark.asyncio
async def test_timeout_from_backend_is_fail() -> None:
    """Backend returns exit_code=-1 on timeout; check reports it as fail."""
    stub = _stub("10.0.0.1", "sleep 30", Response(exit_code=-1, stderr="timeout"))
    result = await SshCommandCheck().run(
        target="10.0.0.1", command="sleep 30", username="root",
        ssh_auth=AUTH, ssh_client=stub, timeout_seconds=1.0,
    )
    assert result.status == "fail"
    assert "timeout" in (result.detail or "")


@pytest.mark.asyncio
async def test_stub_records_port_and_timeout() -> None:
    stub = _stub("10.0.0.1", "echo", Response(exit_code=0))
    await SshCommandCheck().run(
        target="10.0.0.1", command="echo", username="admin",
        ssh_auth=AUTH, ssh_client=stub,
        port=2222, timeout_seconds=15.0,
    )
    assert stub.calls[0]["port"] == 2222
    assert stub.calls[0]["timeout"] == 15.0
    assert stub.calls[0]["username"] == "admin"
