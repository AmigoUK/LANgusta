"""HTTP(S) status-code check.

TLS policy: certificate verification is on by default. A check may opt out
via `insecure_tls=True` in its config kwargs — intended for intentionally
self-signed lab targets; plumbing per-check storage of the flag is a
later migration.
"""

from __future__ import annotations

import httpx

from langusta.monitor.checks.base import CheckResult


async def _http_get(url: str, *, timeout: float, verify: bool):
    async with httpx.AsyncClient(verify=verify, follow_redirects=False) as client:
        return await client.get(url, timeout=timeout)


class HttpCheck:
    async def run(self, *, target: str, **config: object) -> CheckResult:
        port = int(config.get("port", 80))  # type: ignore[arg-type]
        path = str(config.get("path", "/")) or "/"
        timeout = float(config.get("timeout", 5.0))  # type: ignore[arg-type]
        verify = not bool(config.get("insecure_tls", False))
        scheme = "https" if port in (443, 8443) else "http"
        url = f"{scheme}://{target}:{port}{path}"
        try:
            resp = await _http_get(url, timeout=timeout, verify=verify)
        except Exception as exc:
            return CheckResult(status="fail", latency_ms=None, detail=str(exc) or "request failed")
        latency_ms = resp.elapsed.total_seconds() * 1000.0
        if 200 <= resp.status_code < 400:
            return CheckResult(status="ok", latency_ms=latency_ms, detail=None)
        return CheckResult(
            status="fail", latency_ms=latency_ms,
            detail=f"HTTP {resp.status_code}",
        )
