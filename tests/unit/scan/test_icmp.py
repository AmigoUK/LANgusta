"""ICMP scanner tests.

The scanner has two concerns we test independently:
  - `expand_target`: CIDR → list[str] (pure, fast).
  - `ping_sweep`: list[str] → list[PingResult] (async, network-facing).

For `ping_sweep` we inject the icmplib entrypoint so tests never emit real
packets. Real-network smoke lives in an opt-in integration test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from langusta.scan.icmp import PingResult, expand_target, ping_sweep

# ---------------------------------------------------------------------------
# expand_target — CIDR / IP / hostname list
# ---------------------------------------------------------------------------


def test_expand_target_single_ipv4() -> None:
    assert expand_target("192.168.1.1") == ["192.168.1.1"]


def test_expand_target_slash_30_yields_two_usable_hosts() -> None:
    # /30 has 4 addresses: network, 2 usable, broadcast. We ping usable only.
    result = expand_target("192.168.1.0/30")
    assert result == ["192.168.1.1", "192.168.1.2"]


def test_expand_target_slash_32_yields_the_address() -> None:
    assert expand_target("192.168.1.5/32") == ["192.168.1.5"]


def test_expand_target_slash_24_yields_254_hosts() -> None:
    hosts = expand_target("10.0.0.0/24")
    assert len(hosts) == 254
    assert "10.0.0.1" in hosts
    assert "10.0.0.254" in hosts
    assert "10.0.0.0" not in hosts
    assert "10.0.0.255" not in hosts


def test_expand_target_rejects_ipv6_for_v1() -> None:
    """IPv6 scanning is post-v1 (spec doc 1 §4 Pillar B)."""
    with pytest.raises(ValueError, match="IPv6"):
        expand_target("::1/128")


def test_expand_target_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        expand_target("not-an-ip")


# ---------------------------------------------------------------------------
# ping_sweep — injected icmplib
# ---------------------------------------------------------------------------


@dataclass
class _FakeHost:
    """Stub matching icmplib's Host surface area."""

    address: str
    is_alive: bool
    avg_rtt: float


async def _fake_icmplib_success(addresses: list[str], **_: object) -> list[_FakeHost]:
    # Simulate every-other host responding.
    return [
        _FakeHost(address=a, is_alive=(i % 2 == 0), avg_rtt=1.5 if i % 2 == 0 else 0.0)
        for i, a in enumerate(addresses)
    ]


async def _fake_icmplib_all_dead(addresses: list[str], **_: object) -> list[_FakeHost]:
    return [_FakeHost(address=a, is_alive=False, avg_rtt=0.0) for a in addresses]


@pytest.mark.asyncio
async def test_ping_sweep_returns_alive_hosts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """ping_sweep drops dead hosts from the result."""
    import langusta.scan.icmp as icmp_module

    monkeypatch.setattr(icmp_module, "_async_multiping", _fake_icmplib_success)

    targets = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4"]
    results = await ping_sweep(targets)

    alive_ips = {r.address for r in results}
    assert alive_ips == {"10.0.0.1", "10.0.0.3"}
    for r in results:
        assert isinstance(r, PingResult)
        assert r.is_alive is True


@pytest.mark.asyncio
async def test_ping_sweep_on_no_alive_hosts_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langusta.scan.icmp as icmp_module

    monkeypatch.setattr(icmp_module, "_async_multiping", _fake_icmplib_all_dead)

    assert await ping_sweep(["10.0.0.1", "10.0.0.2"]) == []


@pytest.mark.asyncio
async def test_ping_sweep_empty_target_list_returns_empty() -> None:
    """Don't even invoke icmplib if we have nothing to ping."""
    assert await ping_sweep([]) == []


@pytest.mark.asyncio
async def test_ping_sweep_passes_unprivileged_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0004 assumes unprivileged ICMP; we must not accidentally flip this."""
    import langusta.scan.icmp as icmp_module

    seen_kwargs: dict[str, object] = {}

    async def capture(addresses, **kwargs):
        seen_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(icmp_module, "_async_multiping", capture)
    await ping_sweep(["10.0.0.1"])

    assert seen_kwargs.get("privileged") is False
