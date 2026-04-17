"""ICMP liveness check via icmplib (unprivileged)."""

from __future__ import annotations

from icmplib import async_ping as _async_ping

from langusta.monitor.checks.base import CheckResult


class IcmpCheck:
    async def run(self, *, target: str, **_: object) -> CheckResult:
        try:
            host = await _async_ping(
                target, count=1, timeout=1.0, privileged=False,
            )
        except Exception as exc:
            return CheckResult(status="fail", latency_ms=None, detail=str(exc))
        if host.is_alive:
            return CheckResult(status="ok", latency_ms=float(host.avg_rtt), detail=None)
        return CheckResult(status="fail", latency_ms=None, detail="no response")
