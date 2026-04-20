"""mDNS discovery tests.

The zeroconf integration is mocked out — tests exercise the filtering and
timeout wiring, not the actual mDNS protocol. A real-network integration
test lives behind `@pytest.mark.integration` (spec §12).
"""

from __future__ import annotations

import pytest

from langusta.scan.mdns import MdnsRecord, discover


async def _static_records(timeout: float):
    """Return a fixed set of records regardless of timeout."""
    assert timeout > 0
    return [
        MdnsRecord(ip="10.0.0.1", name="router.local", service_type="_http._tcp.local."),
        MdnsRecord(ip="10.0.0.5", name="printer.local", service_type="_ipp._tcp.local."),
        MdnsRecord(ip="10.0.0.20", name="tv.local", service_type="_airplay._tcp.local."),
    ]


async def _empty_records(timeout: float):
    return []


@pytest.mark.asyncio
async def test_discover_returns_ip_to_primary_name_map() -> None:
    result = await discover(browser_fn=_static_records, timeout=0.1)
    assert result == {
        "10.0.0.1": "router.local",
        "10.0.0.5": "printer.local",
        "10.0.0.20": "tv.local",
    }


@pytest.mark.asyncio
async def test_discover_filters_by_target_ips() -> None:
    result = await discover(
        target_ips={"10.0.0.1", "10.0.0.5"},
        browser_fn=_static_records,
        timeout=0.1,
    )
    assert result == {"10.0.0.1": "router.local", "10.0.0.5": "printer.local"}


@pytest.mark.asyncio
async def test_discover_empty_target_set_returns_empty() -> None:
    """Explicit empty target set means 'nothing to enrich' — don't even call the browser."""
    result = await discover(target_ips=set(), browser_fn=_static_records, timeout=0.1)
    assert result == {}


@pytest.mark.asyncio
async def test_discover_empty_records_returns_empty() -> None:
    assert await discover(browser_fn=_empty_records, timeout=0.1) == {}


@pytest.mark.asyncio
async def test_discover_first_record_per_ip_wins() -> None:
    """If the same IP announces under multiple service types, keep the first
    name seen — deterministic and cheap."""

    async def dup(timeout: float):
        return [
            MdnsRecord(ip="10.0.0.1", name="first.local", service_type="_ssh._tcp.local."),
            MdnsRecord(ip="10.0.0.1", name="second.local", service_type="_http._tcp.local."),
        ]

    result = await discover(browser_fn=dup, timeout=0.1)
    assert result == {"10.0.0.1": "first.local"}


@pytest.mark.asyncio
async def test_discover_browser_exception_returns_empty() -> None:
    """A flaky mDNS stack shouldn't fail the scan."""

    async def raises(timeout: float):
        raise OSError("mdns broken")

    assert await discover(browser_fn=raises, timeout=0.1) == {}


@pytest.mark.asyncio
async def test_real_browse_handler_accepts_zeroconf_0148_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: zeroconf 0.148 renamed the handler's zeroconf kwarg from
    `zc` to `zeroconf`, producing `TypeError: unexpected keyword argument
    'zeroconf'` in real scans. Verify our handler accepts the new shape."""
    from langusta.scan import mdns as mdns_mod

    captured: dict[str, object] = {}

    class _FakeAsyncZeroconf:
        def __init__(self) -> None:
            self.zeroconf = object()

        async def async_close(self) -> None:
            pass

    class _FakeBrowser:
        def __init__(self, zc: object, types: list[str], *, handlers: list) -> None:
            captured["handlers"] = handlers

        async def async_cancel(self) -> None:
            pass

    class _FakeInfo:
        def __init__(self, service_type: str, name: str) -> None:
            self.service_type = service_type
            self.name = name
            self.addresses: list[bytes] = []
            self.server: str | None = None

        async def async_request(self, zc: object, timeout: int) -> None:
            # No addresses → handler returns cleanly; we just need to confirm
            # it didn't raise TypeError before getting here.
            captured["received_zc"] = zc
            captured["async_request_called"] = True

    monkeypatch.setattr(mdns_mod, "asyncio", __import__("asyncio"))
    monkeypatch.setattr(
        "zeroconf.asyncio.AsyncZeroconf", _FakeAsyncZeroconf,
    )
    monkeypatch.setattr(
        "zeroconf.asyncio.AsyncServiceBrowser", _FakeBrowser,
    )
    monkeypatch.setattr(
        "zeroconf.asyncio.AsyncServiceInfo", _FakeInfo,
    )

    async def _fast_sleep(timeout: float) -> None:
        # Fire the captured handler with the 0.148 kwarg shape before returning.
        [handler] = captured["handlers"]  # type: ignore[misc]
        await handler(
            zeroconf=object(),
            service_type="_http._tcp.local.",
            name="router._http._tcp.local.",
            state_change="Added",
        )

    monkeypatch.setattr(mdns_mod.asyncio, "sleep", _fast_sleep)

    # The function returns [] because the fake info has no addresses — but
    # crucially it must not raise TypeError on the kwarg mismatch.
    result = await mdns_mod._real_browse(timeout=0.01)
    assert result == []
    assert captured.get("async_request_called") is True
