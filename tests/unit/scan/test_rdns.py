"""Reverse DNS tests — thin asyncio wrapper over socket.gethostbyaddr."""

from __future__ import annotations

import socket

import pytest

from langusta.scan.rdns import resolve_many, resolve_one


@pytest.mark.asyncio
async def test_resolve_one_returns_hostname_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ip: str) -> tuple[str, list[str], list[str]]:
        return ("router.local", [], [ip])

    monkeypatch.setattr("langusta.scan.rdns._gethostbyaddr", fake)
    assert await resolve_one("10.0.0.1") == "router.local"


@pytest.mark.asyncio
async def test_resolve_one_returns_none_on_nxdomain(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ip: str):
        raise socket.herror("unknown host")

    monkeypatch.setattr("langusta.scan.rdns._gethostbyaddr", fake)
    assert await resolve_one("10.0.0.99") is None


@pytest.mark.asyncio
async def test_resolve_one_returns_none_on_gaierror(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ip: str):
        raise socket.gaierror("dns broken")

    monkeypatch.setattr("langusta.scan.rdns._gethostbyaddr", fake)
    assert await resolve_one("10.0.0.99") is None


@pytest.mark.asyncio
async def test_resolve_one_times_out_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slow DNS server shouldn't block the scan."""
    import time

    def slow(ip: str):
        time.sleep(0.3)
        return ("slow", [], [ip])

    monkeypatch.setattr("langusta.scan.rdns._gethostbyaddr", slow)
    result = await resolve_one("10.0.0.1", timeout=0.05)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_many_returns_ip_name_map(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ip: str) -> tuple[str, list[str], list[str]]:
        return ({"10.0.0.1": "alpha.local", "10.0.0.2": "bravo.local"}[ip], [], [ip])

    monkeypatch.setattr("langusta.scan.rdns._gethostbyaddr", fake)
    result = await resolve_many({"10.0.0.1", "10.0.0.2"})
    assert result == {"10.0.0.1": "alpha.local", "10.0.0.2": "bravo.local"}


@pytest.mark.asyncio
async def test_resolve_many_omits_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(ip: str):
        if ip == "10.0.0.1":
            return ("alpha.local", [], [ip])
        raise socket.herror()

    monkeypatch.setattr("langusta.scan.rdns._gethostbyaddr", fake)
    result = await resolve_many({"10.0.0.1", "10.0.0.2"})
    assert result == {"10.0.0.1": "alpha.local"}


@pytest.mark.asyncio
async def test_resolve_many_empty_input_returns_empty() -> None:
    assert await resolve_many(set()) == {}
