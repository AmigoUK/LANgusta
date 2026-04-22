"""Monitor check implementations — ICMP / TCP / HTTP.

Each check must:
  - Return a CheckResult dataclass (status='ok'|'fail', latency_ms, detail).
  - Never raise — even on timeout, refused connection, or DNS error.
  - Accept the minimal config it needs (target IP + optional port/path).
"""

from __future__ import annotations

import pytest

from langusta.monitor.checks.base import CheckResult
from langusta.monitor.checks.http import HttpCheck
from langusta.monitor.checks.icmp import IcmpCheck
from langusta.monitor.checks.tcp import TcpCheck

# ---------------------------------------------------------------------------
# ICMP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_icmp_success_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Host:
        address = "10.0.0.1"
        is_alive = True
        avg_rtt = 2.5

    async def fake(ip: str, **_):
        return _Host()

    monkeypatch.setattr("langusta.monitor.checks.icmp._async_ping", fake)
    result = await IcmpCheck().run(target="10.0.0.1")
    assert result.status == "ok"
    assert result.latency_ms == 2.5


@pytest.mark.asyncio
async def test_icmp_dead_host_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Host:
        address = "10.0.0.1"
        is_alive = False
        avg_rtt = 0.0

    async def fake(ip: str, **_):
        return _Host()

    monkeypatch.setattr("langusta.monitor.checks.icmp._async_ping", fake)
    result = await IcmpCheck().run(target="10.0.0.1")
    assert result.status == "fail"
    assert result.latency_ms is None


@pytest.mark.asyncio
async def test_icmp_exception_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(ip: str, **_):
        raise OSError("no route")

    monkeypatch.setattr("langusta.monitor.checks.icmp._async_ping", fake)
    result = await IcmpCheck().run(target="10.0.0.1")
    assert result.status == "fail"
    assert result.detail is not None


# ---------------------------------------------------------------------------
# TCP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tcp_connect_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Writer:
        def close(self) -> None: ...
        async def wait_closed(self) -> None: ...

    async def fake(target, port, *, timeout):
        return object(), _Writer()

    monkeypatch.setattr("langusta.monitor.checks.tcp._open_connection", fake)
    result = await TcpCheck().run(target="10.0.0.1", port=22)
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_tcp_refused_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(target, port, *, timeout):
        raise ConnectionRefusedError

    monkeypatch.setattr("langusta.monitor.checks.tcp._open_connection", fake)
    result = await TcpCheck().run(target="10.0.0.1", port=22)
    assert result.status == "fail"


@pytest.mark.asyncio
async def test_tcp_timeout_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(target, port, *, timeout):
        raise TimeoutError

    monkeypatch.setattr("langusta.monitor.checks.tcp._open_connection", fake)
    result = await TcpCheck().run(target="10.0.0.1", port=22)
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_200_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 200
        elapsed = _ElapsedDummy(millis=42.0)

    async def fake_get(url, *, timeout, verify):
        return _Resp()

    monkeypatch.setattr("langusta.monitor.checks.http._http_get", fake_get)
    result = await HttpCheck().run(target="10.0.0.1", port=80)
    assert result.status == "ok"
    assert result.latency_ms == 42.0


@pytest.mark.asyncio
async def test_http_500_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status_code = 503
        elapsed = _ElapsedDummy(millis=5.0)

    async def fake_get(url, *, timeout, verify):
        return _Resp()

    monkeypatch.setattr("langusta.monitor.checks.http._http_get", fake_get)
    result = await HttpCheck().run(target="10.0.0.1", port=80)
    assert result.status == "fail"
    assert "503" in (result.detail or "")


@pytest.mark.asyncio
async def test_http_connection_error_returns_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(url, *, timeout, verify):
        raise ConnectionError("refused")

    monkeypatch.setattr("langusta.monitor.checks.http._http_get", fake_get)
    result = await HttpCheck().run(target="10.0.0.1", port=80)
    assert result.status == "fail"


@pytest.mark.asyncio
async def test_http_builds_url_with_port_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class _Resp:
        status_code = 200
        elapsed = _ElapsedDummy(millis=1.0)

    async def fake_get(url, *, timeout, verify):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("langusta.monitor.checks.http._http_get", fake_get)
    await HttpCheck().run(target="10.0.0.1", port=8443, path="/healthz")
    assert seen["url"] == "https://10.0.0.1:8443/healthz"


@pytest.mark.asyncio
async def test_http_uses_plain_http_for_port_80(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class _Resp:
        status_code = 200
        elapsed = _ElapsedDummy(millis=1.0)

    async def fake_get(url, *, timeout, verify):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("langusta.monitor.checks.http._http_get", fake_get)
    await HttpCheck().run(target="10.0.0.1", port=80)
    assert seen["url"].startswith("http://")


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


def test_check_result_fields() -> None:
    r = CheckResult(status="ok", latency_ms=1.0, detail=None)
    assert r.status == "ok"
    assert r.latency_ms == 1.0


class _ElapsedDummy:
    """Mimic httpx's Response.elapsed (a timedelta-ish object)."""

    def __init__(self, millis: float) -> None:
        self.total_seconds = lambda: millis / 1000.0
