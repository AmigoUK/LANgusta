"""Integration test for `langusta import-netbox`.

The HTTP getter is monkey-patched on the import module so tests stay offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import assets as assets_dal
from langusta.db.connection import connect

runner = CliRunner()

PW = "master-password-for-netbox-cli-tests"


def _env(home: Path, *, token: str = "netbox-token-xyz") -> dict[str, str]:
    return {
        "HOME": str(home),
        "LANGUSTA_MASTER_PASSWORD": PW,
        "LANGUSTA_NETBOX_TOKEN": token,
    }


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir(parents=True)
    runner.invoke(app, ["init"], env=_env(h))
    return h


def _page(results):
    return {"count": len(results), "next": None, "previous": None, "results": results}


def test_import_netbox_populates_inventory(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(url: str, *, token: str):
        assert token == "netbox-token-xyz"
        return _page([
            {
                "id": 1, "name": "core-router",
                "primary_ip4": {"address": "10.0.0.1/24"},
                "device_type": {
                    "model": "C3750",
                    "manufacturer": {"name": "Cisco"},
                },
            }
        ])

    monkeypatch.setattr(
        "langusta.db.import_netbox.default_http_get", fake_get,
    )
    r = runner.invoke(
        app, ["import-netbox", "--url", "https://netbox.example.com"],
        env=_env(home),
    )
    assert r.exit_code == 0, r.stdout
    assert "imported 1" in r.stdout.lower() or "1 imported" in r.stdout.lower()
    with connect(home / ".langusta" / "db.sqlite") as conn:
        [asset] = assets_dal.list_all(conn)
    assert asset.hostname == "core-router"
    assert asset.primary_ip == "10.0.0.1"
    assert asset.source == "imported"


def test_import_netbox_without_token_env_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = runner.invoke(
        app, ["import-netbox", "--url", "https://netbox.example.com"],
        env={"HOME": str(home), "LANGUSTA_MASTER_PASSWORD": PW},
    )
    assert r.exit_code != 0
    assert "token" in (r.stdout + (r.stderr or "")).lower()


def test_import_netbox_auth_error_surfaces_cleanly(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(url: str, *, token: str):
        from langusta.db.import_netbox import NetBoxAuthError
        raise NetBoxAuthError("401 Unauthorized")

    monkeypatch.setattr(
        "langusta.db.import_netbox.default_http_get", fake_get,
    )
    r = runner.invoke(
        app, ["import-netbox", "--url", "https://netbox.example.com"],
        env=_env(home),
    )
    assert r.exit_code != 0
    assert "auth" in (r.stdout + (r.stderr or "")).lower() or "401" in (r.stdout + (r.stderr or ""))


def test_import_netbox_surfaces_network_error_with_exit_1(
    home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave-3 TEST-T-017. NetBoxAuthError is already covered above;
    NetBoxNetworkError (connection refused, timeout, DNS failure) is
    a separate CLI branch and needs its own smoke test. An operator
    hitting this path wants an exit 1 and a 'network error' prefix,
    not a raw httpx traceback."""
    async def fake_get(url: str, *, token: str) -> object:
        from langusta.db.import_netbox import NetBoxNetworkError

        raise NetBoxNetworkError("connection timed out after 5.0s")

    monkeypatch.setattr(
        "langusta.db.import_netbox.default_http_get", fake_get,
    )

    r = runner.invoke(
        app, ["import-netbox", "--url", "https://netbox.example.com"],
        env=_env(home),
    )

    assert r.exit_code == 1, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    combined = (r.stdout + (r.stderr or "")).lower()
    assert "network error" in combined, (
        f"import-netbox should surface a 'network error' prefix; got "
        f"{combined!r}"
    )
    assert "traceback" not in combined, (
        f"import-netbox leaked a raw traceback on NetBoxNetworkError; "
        f"got {combined!r}"
    )
