"""SSH-command monitor check kind.

Runs a shell command over SSH and evaluates success by exit code + optional
stdout regex. The runner injects `ssh_auth` and `ssh_client`; the check
itself is stateless.
"""

from __future__ import annotations

import re

from langusta.monitor.checks.base import CheckResult
from langusta.monitor.ssh.auth import SshKeyAuth, SshPasswordAuth
from langusta.monitor.ssh.client import SshClient

_DETAIL_MAX = 200
_DEFAULT_PORT = 22
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_SUCCESS_EXIT = 0


class SshCommandCheck:
    async def run(self, *, target: str, **config: object) -> CheckResult:
        command = _required_str(config, "command")
        username = _required_str(config, "username")
        ssh_auth = config.get("ssh_auth")
        ssh_client = config.get("ssh_client")
        if not isinstance(ssh_auth, (SshKeyAuth, SshPasswordAuth)):
            return CheckResult(status="fail", latency_ms=None, detail="ssh_auth missing")
        if not isinstance(ssh_client, SshClient):
            return CheckResult(status="fail", latency_ms=None, detail="ssh_client missing")

        port = int(config.get("port") or _DEFAULT_PORT)
        timeout = float(config.get("timeout_seconds") or _DEFAULT_TIMEOUT)
        success_exit = _as_int(config.get("success_exit_code"), _DEFAULT_SUCCESS_EXIT)
        stdout_pattern = _optional_str(config, "stdout_pattern")

        result = await ssh_client.run_command(
            target, port=port, username=username, auth=ssh_auth,
            command=command, timeout=timeout,
        )

        if result.exit_code != success_exit:
            detail = f"exit {result.exit_code}"
            if result.stderr:
                detail = f"{detail}: {_truncate(result.stderr)}"
            return CheckResult(status="fail", latency_ms=result.elapsed_ms, detail=detail)

        if stdout_pattern is not None:
            try:
                if not re.search(stdout_pattern, result.stdout):
                    return CheckResult(
                        status="fail", latency_ms=result.elapsed_ms,
                        detail=f"stdout did not match /{stdout_pattern}/",
                    )
            except re.error as exc:
                return CheckResult(
                    status="fail", latency_ms=result.elapsed_ms,
                    detail=f"bad stdout_pattern: {exc}",
                )

        preview = _truncate(result.stdout.strip()) if result.stdout else None
        return CheckResult(status="ok", latency_ms=result.elapsed_ms, detail=preview)


def _required_str(config: dict[str, object], key: str) -> str:
    v = config.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(f"ssh_command check requires config[{key!r}]")
    return v


def _optional_str(config: dict[str, object], key: str) -> str | None:
    v = config.get(key)
    return v if isinstance(v, str) and v else None


def _as_int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _truncate(value: str) -> str:
    if len(value) <= _DETAIL_MAX:
        return value
    return value[: _DETAIL_MAX - 1] + "…"
