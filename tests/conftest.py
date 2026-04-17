"""Shared pytest fixtures for the LANgusta test suite."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _offline_scan_enrichments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test offline by default.

    Scanner enrichments (rDNS, TCP probe, mDNS) return empty unless a test
    overrides them. Tests that want real behaviour can `monkeypatch` the
    binding back to the real function, but none do in v1.
    """

    async def empty_rdns(ips, **_):
        return {}

    async def empty_tcp(ips, **_):
        return {}

    async def empty_mdns(target_ips=None, **_):
        return {}

    monkeypatch.setattr(
        "langusta.scan.orchestrator.resolve_many", empty_rdns, raising=False,
    )
    monkeypatch.setattr(
        "langusta.scan.orchestrator.probe_ports_many", empty_tcp, raising=False,
    )
    monkeypatch.setattr(
        "langusta.scan.orchestrator.mdns_discover", empty_mdns, raising=False,
    )


@pytest.fixture
def tmp_langusta_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect ~/.langusta/ to a per-test tmp directory.

    Every test that creates the DB, backups, or config must use this fixture so
    nothing escapes to the real user's home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Windows-style HOME is ignored here (M0 targets Linux + macOS per ADR-0004).
    langusta_dir = home / ".langusta"
    yield langusta_dir
    # Belt-and-braces: clean in case something leaked.
    if langusta_dir.exists():
        for child in langusta_dir.rglob("*"):
            if child.is_file():
                os.chmod(child, 0o644)
