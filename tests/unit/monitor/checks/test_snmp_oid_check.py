"""SnmpOidCheck unit tests."""

from __future__ import annotations

import pytest

from langusta.monitor.checks.snmp_oid import SnmpOidCheck
from langusta.scan.snmp.auth import SnmpV2cAuth
from langusta.scan.snmp.client import SnmpClient

V2C = SnmpV2cAuth(community="public")


class _StubClient:
    """Pre-programmed SnmpClient stub: returns value-or-None per OID."""

    def __init__(self, responses: dict[str, str | None]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    async def get_sys_descr(self, ip, *, auth, timeout=2.0):
        return await self.get(ip, "1.3.6.1.2.1.1.1.0", auth=auth, timeout=timeout)

    async def get(self, ip, oid, *, auth, timeout=2.0):
        self.calls.append((ip, oid))
        return self.responses.get(oid)


def _assert_protocol() -> None:
    assert isinstance(_StubClient({}), SnmpClient)


_assert_protocol()


@pytest.mark.asyncio
async def test_no_response_fails_with_timeout_detail() -> None:
    client = _StubClient({})  # nothing for any OID
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="1.3.6.1.2.1.1.3.0",
        snmp_auth=V2C, snmp_client=client,
    )
    assert result.status == "fail"
    assert "timeout" in (result.detail or "") or "no response" in (result.detail or "")


@pytest.mark.asyncio
async def test_no_comparator_any_value_is_ok() -> None:
    client = _StubClient({"1.3.6.1.2.1.1.3.0": "12345"})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="1.3.6.1.2.1.1.3.0",
        snmp_auth=V2C, snmp_client=client,
    )
    assert result.status == "ok"
    assert result.detail == "12345"
    assert client.calls == [("10.0.0.1", "1.3.6.1.2.1.1.3.0")]


@pytest.mark.asyncio
async def test_eq_comparator_match_is_ok() -> None:
    client = _StubClient({"1.3.6.1.4.1.9.9.13.1.5.1.3.1": "cisco-router"})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="1.3.6.1.4.1.9.9.13.1.5.1.3.1",
        snmp_auth=V2C, snmp_client=client,
        expected_value="cisco-router", comparator="eq",
    )
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_eq_comparator_mismatch_is_fail() -> None:
    client = _StubClient({"1.3.6.1.4.1.9.9.13.1.5.1.3.1": "surprise"})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="1.3.6.1.4.1.9.9.13.1.5.1.3.1",
        snmp_auth=V2C, snmp_client=client,
        expected_value="cisco-router", comparator="eq",
    )
    assert result.status == "fail"
    assert "surprise" in (result.detail or "")


@pytest.mark.asyncio
async def test_contains_comparator() -> None:
    client = _StubClient({"oid.1": "Cisco IOS 15.2(4)E9"})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.1", snmp_auth=V2C, snmp_client=client,
        expected_value="Cisco", comparator="contains",
    )
    assert result.status == "ok"
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.1", snmp_auth=V2C, snmp_client=client,
        expected_value="MikroTik", comparator="contains",
    )
    assert result.status == "fail"


@pytest.mark.asyncio
async def test_gt_lt_comparators_on_numeric_values() -> None:
    client = _StubClient({"oid.2": "42"})
    ok = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.2", snmp_auth=V2C, snmp_client=client,
        expected_value="10", comparator="gt",
    )
    assert ok.status == "ok"
    fail = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.2", snmp_auth=V2C, snmp_client=client,
        expected_value="99", comparator="gt",
    )
    assert fail.status == "fail"
    ok = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.2", snmp_auth=V2C, snmp_client=client,
        expected_value="99", comparator="lt",
    )
    assert ok.status == "ok"


@pytest.mark.asyncio
async def test_gt_on_non_numeric_value_fails_cleanly() -> None:
    client = _StubClient({"oid.3": "not-a-number"})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.3", snmp_auth=V2C, snmp_client=client,
        expected_value="10", comparator="gt",
    )
    assert result.status == "fail"


@pytest.mark.asyncio
async def test_neq_comparator() -> None:
    client = _StubClient({"oid.4": "UP"})
    ok = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.4", snmp_auth=V2C, snmp_client=client,
        expected_value="DOWN", comparator="neq",
    )
    assert ok.status == "ok"
    fail = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.4", snmp_auth=V2C, snmp_client=client,
        expected_value="UP", comparator="neq",
    )
    assert fail.status == "fail"


@pytest.mark.asyncio
async def test_missing_snmp_auth_returns_fail() -> None:
    client = _StubClient({})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.1",
        snmp_auth=None, snmp_client=client,
    )
    assert result.status == "fail"
    assert "snmp_auth" in (result.detail or "")


@pytest.mark.asyncio
async def test_long_value_is_truncated_in_detail() -> None:
    long_val = "x" * 500
    client = _StubClient({"oid.long": long_val})
    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.long", snmp_auth=V2C, snmp_client=client,
    )
    assert result.status == "ok"
    assert result.detail is not None
    assert len(result.detail) <= 201  # _DETAIL_MAX + '…'


@pytest.mark.asyncio
async def test_client_exception_returns_fail() -> None:
    class _BrokenClient:
        async def get_sys_descr(self, ip, *, auth, timeout=2.0):
            raise RuntimeError("boom")

        async def get(self, ip, oid, *, auth, timeout=2.0):
            raise RuntimeError("boom")

    result = await SnmpOidCheck().run(
        target="10.0.0.1", oid="oid.x", snmp_auth=V2C, snmp_client=_BrokenClient(),
    )
    assert result.status == "fail"
    assert "boom" in (result.detail or "")
