"""TCP port probe tests.

The probe uses `asyncio.open_connection` with a short timeout. Tests inject
a fake connection function so we never touch the network.
"""

from __future__ import annotations

import asyncio

import pytest

from langusta.scan.tcp import DEFAULT_TOP_PORTS, probe_ports, probe_ports_many


class _FakeWriter:
    def close(self) -> None:
        ...

    async def wait_closed(self) -> None:
        ...


class _FakeReader:
    ...


def _install_tcp_oracle(
    monkeypatch: pytest.MonkeyPatch,
    oracle: dict[tuple[str, int], bool],
) -> None:
    """Monkey-patch `_open_connection` so only (ip, port) pairs in `oracle`
    with True value succeed; everything else raises ConnectionRefusedError."""

    async def fake(ip: str, port: int, *, timeout: float):
        if oracle.get((ip, port), False):
            return _FakeReader(), _FakeWriter()
        raise ConnectionRefusedError

    monkeypatch.setattr("langusta.scan.tcp._open_connection", fake)


@pytest.mark.asyncio
async def test_probe_ports_returns_only_open_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_tcp_oracle(monkeypatch, {
        ("10.0.0.1", 22): True,
        ("10.0.0.1", 80): False,
        ("10.0.0.1", 443): True,
    })
    result = await probe_ports("10.0.0.1", ports=(22, 80, 443))
    assert result == frozenset({22, 443})


@pytest.mark.asyncio
async def test_probe_ports_timeout_counts_as_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_times_out(ip: str, port: int, *, timeout: float):
        await asyncio.sleep(timeout + 0.05)
        raise TimeoutError

    monkeypatch.setattr("langusta.scan.tcp._open_connection", always_times_out)
    result = await probe_ports("10.0.0.1", ports=(22,), timeout=0.05)
    assert result == frozenset()


@pytest.mark.asyncio
async def test_probe_ports_on_empty_port_list_returns_empty() -> None:
    assert await probe_ports("10.0.0.1", ports=()) == frozenset()


@pytest.mark.asyncio
async def test_probe_ports_many_fans_out_per_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_tcp_oracle(monkeypatch, {
        ("10.0.0.1", 22): True,
        ("10.0.0.2", 80): True,
        ("10.0.0.2", 443): True,
    })
    result = await probe_ports_many({"10.0.0.1", "10.0.0.2"}, ports=(22, 80, 443))
    assert result == {
        "10.0.0.1": frozenset({22}),
        "10.0.0.2": frozenset({80, 443}),
    }


@pytest.mark.asyncio
async def test_probe_ports_many_omits_hosts_with_no_open_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_tcp_oracle(monkeypatch, {("10.0.0.1", 22): True})
    result = await probe_ports_many({"10.0.0.1", "10.0.0.2"}, ports=(22,))
    assert result == {"10.0.0.1": frozenset({22})}


def test_default_top_ports_includes_common() -> None:
    assert 22 in DEFAULT_TOP_PORTS
    assert 80 in DEFAULT_TOP_PORTS
    assert 443 in DEFAULT_TOP_PORTS
    assert len(DEFAULT_TOP_PORTS) <= 200  # keep scan sweep reasonable
    assert len(DEFAULT_TOP_PORTS) >= 20   # and not trivially small
