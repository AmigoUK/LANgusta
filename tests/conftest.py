"""Shared pytest fixtures for the LANgusta test suite."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


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
