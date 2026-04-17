"""HTTP(S) status-code check."""

from __future__ import annotations

import httpx

from langusta.monitor.checks.base import CheckResult


async def _http_get(url: str, *, timeout: float):
    async with httpx.AsyncClient(verify=False, follow_redirects=False) as client:
        return await client.get(url, timeout=timeout)


class HttpCheck:
    async def run(self, *, target: str, **config: object) -> CheckResult:
        port = int(config.get("port", 80))
        path = str(config.get("path", "/")) or "/"
        timeout = float(config.get("timeout", 5.0))
        scheme = "https" if port in (443, 8443) else "http"
        url = f"{scheme}://{target}:{port}{path}"
        try:
            resp = await _http_get(url, timeout=timeout)
        except Exception as exc:
            return CheckResult(status="fail", latency_ms=None, detail=str(exc) or "request failed")
        latency_ms = resp.elapsed.total_seconds() * 1000.0
        if 200 <= resp.status_code < 400:
            return CheckResult(status="ok", latency_ms=latency_ms, detail=None)
        return CheckResult(
            status="fail", latency_ms=latency_ms,
            detail=f"HTTP {resp.status_code}",
        )
