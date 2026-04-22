"""HttpCheck TLS verification surface.

Wave-3 TEST-M-001 (finding M-001): `verify=False` was hardcoded in
`langusta.monitor.checks.http._http_get`, silently disabling certificate
verification on every HTTPS probe. Three independent review lenses
(correctness / security / architecture) flagged the same line.

The default must verify. An explicit `insecure_tls=True` kwarg is the
escape hatch for intentionally-self-signed lab targets; plumbing to
store it per-check (schema column) is a separate, later migration.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from langusta.monitor.checks.http import HttpCheck


class _CapturingClient:
    """Stand-in for httpx.AsyncClient that records constructor kwargs and
    returns a deterministic 200 response."""

    captured_kwargs: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).captured_kwargs = dict(kwargs)

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get(self, url: str, *, timeout: float) -> Any:
        class _Resp:
            status_code = 200

            class _Elapsed:
                @staticmethod
                def total_seconds() -> float:
                    return 0.001

            elapsed = _Elapsed

        return _Resp()


@pytest.mark.asyncio
async def test_httpcheck_defaults_to_verify_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTPS probe verifies the server certificate by default."""
    _CapturingClient.captured_kwargs = {}
    monkeypatch.setattr(
        "langusta.monitor.checks.http.httpx.AsyncClient", _CapturingClient,
    )

    await HttpCheck().run(target="example.test", port=443, path="/")

    assert _CapturingClient.captured_kwargs.get("verify") is True, (
        "HttpCheck must default to verify=True; got "
        f"{_CapturingClient.captured_kwargs.get('verify')!r}"
    )


@pytest.mark.asyncio
async def test_httpcheck_honours_insecure_tls_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`insecure_tls=True` on the check config propagates to verify=False."""
    _CapturingClient.captured_kwargs = {}
    monkeypatch.setattr(
        "langusta.monitor.checks.http.httpx.AsyncClient", _CapturingClient,
    )

    await HttpCheck().run(
        target="example.test", port=443, path="/", insecure_tls=True,
    )

    assert _CapturingClient.captured_kwargs.get("verify") is False
