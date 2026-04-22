"""TCP connect check."""

from __future__ import annotations

import contextlib
import time

from langusta.core.net import open_tcp_connection as _open_connection
from langusta.monitor.checks.base import CheckResult

# `_open_connection` is re-exported as a module attribute so existing
# test code can `monkeypatch.setattr("langusta.monitor.checks.tcp.
# _open_connection", fake)`. Wave-3 A-009 dedupe with scan/tcp.py.
__all__ = ["TcpCheck"]


class TcpCheck:
    async def run(self, *, target: str, **config: object) -> CheckResult:
        port = int(config.get("port", 0))
        timeout = float(config.get("timeout", 2.0))
        if port == 0:
            return CheckResult(status="fail", latency_ms=None, detail="no port configured")
        t0 = time.monotonic()
        try:
            _, writer = await _open_connection(target, port, timeout=timeout)
        except (OSError, TimeoutError) as exc:
            return CheckResult(status="fail", latency_ms=None, detail=str(exc) or "connection failed")
        latency_ms = (time.monotonic() - t0) * 1000.0
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return CheckResult(status="ok", latency_ms=latency_ms, detail=None)
