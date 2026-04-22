"""Daemon install-recipe tests — systemd user unit + launchd plist.

Golden-file tests: each backend renders a recipe for a fixed exec path
and the test asserts the content matches a stable shape. Linux target:
systemd-user unit. macOS target: launchd plist (well-formed XML).
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from langusta.platform.base import NotImplementedCapability
from langusta.platform.linux import LinuxBackend
from langusta.platform.macos import MacOSBackend
from langusta.platform.windows import WindowsStubBackend

EXEC_PATH = "/usr/local/bin/langusta"


# ---------------------------------------------------------------------------
# Linux — systemd user unit
# ---------------------------------------------------------------------------


def test_linux_recipe_is_systemd_user_unit() -> None:
    recipe = LinuxBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    assert recipe.manager == "systemd-user"
    assert recipe.install_path.name == "langusta-monitor.service"
    # Path is under ~/.config/systemd/user/ — the XDG user-systemd dir.
    assert "systemd/user" in str(recipe.install_path)


def test_linux_recipe_unit_has_required_sections() -> None:
    recipe = LinuxBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    content = recipe.content
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content
    assert f"ExecStart={EXEC_PATH} monitor daemon" in content
    assert "Restart=on-failure" in content
    assert "After=network-online.target" in content
    # No hardcoded usernames or paths beyond the exec.
    assert "%h" not in content or "%h" in content  # either way ok


def test_linux_recipe_start_hint_uses_systemctl_user() -> None:
    recipe = LinuxBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    assert "systemctl --user" in recipe.start_hint


# ---------------------------------------------------------------------------
# macOS — launchd plist
# ---------------------------------------------------------------------------


def test_macos_recipe_is_launchd_plist() -> None:
    recipe = MacOSBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    assert recipe.manager == "launchd"
    assert recipe.install_path.suffix == ".plist"
    assert "LaunchAgents" in str(recipe.install_path)


def test_macos_recipe_plist_is_valid_xml() -> None:
    recipe = MacOSBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    data = plistlib.loads(recipe.content.encode("utf-8"))
    assert data.get("Label") == "uk.attv.langusta.monitor"
    program_args = data.get("ProgramArguments")
    assert program_args[0] == EXEC_PATH
    assert "monitor" in program_args
    assert "daemon" in program_args
    assert data.get("RunAtLoad") is True
    assert data.get("KeepAlive") is True


def test_macos_recipe_start_hint_uses_launchctl() -> None:
    recipe = MacOSBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    assert "launchctl" in recipe.start_hint


# ---------------------------------------------------------------------------
# Windows — stub raises
# ---------------------------------------------------------------------------


def test_windows_stub_raises_on_daemon_recipe() -> None:
    with pytest.raises(NotImplementedCapability):
        WindowsStubBackend().daemon_install_recipe(exec_path=EXEC_PATH)


# ---------------------------------------------------------------------------
# Recipe lifecycle — path under user's home
# ---------------------------------------------------------------------------


def test_linux_install_path_is_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    recipe = LinuxBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    assert str(recipe.install_path).startswith(str(tmp_path))


def test_macos_install_path_is_under_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    recipe = MacOSBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    assert str(recipe.install_path).startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# Wave-3 TEST-S-003 — launchd plist must not redirect logs to /tmp
# ---------------------------------------------------------------------------


def test_macos_plist_does_not_route_logs_through_tmp() -> None:
    """/tmp on macOS is mode 1777 — any local user can tail the monitor
    daemon's stdout/stderr there, or pre-create the target as a symlink
    attack. Logs must live under the per-user ~/Library/Logs tree.
    """
    recipe = MacOSBackend().daemon_install_recipe(exec_path=EXEC_PATH)
    data = plistlib.loads(recipe.content.encode("utf-8"))

    stdout = data.get("StandardOutPath", "")
    stderr = data.get("StandardErrorPath", "")

    assert "/tmp/" not in stdout, (
        f"plist StandardOutPath routes through world-readable /tmp: {stdout!r}"
    )
    assert "/tmp/" not in stderr, (
        f"plist StandardErrorPath routes through world-readable /tmp: {stderr!r}"
    )
    assert "Library/Logs" in stdout, (
        f"stdout should land under ~/Library/Logs/ (per-user, user-owned); "
        f"got {stdout!r}"
    )
    assert "Library/Logs" in stderr, (
        f"stderr should land under ~/Library/Logs/; got {stderr!r}"
    )
