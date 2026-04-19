"""Unit tests — keybinding preset resolution."""

from __future__ import annotations

import pytest
from textual.binding import Binding

from langusta.tui.keybindings import (
    VIM_PRESET,
    active_preset_from_env,
    resolve_preset,
)


def test_vim_preset_has_core_navigation_keys() -> None:
    keys = {b.key for b in VIM_PRESET}
    assert {"j", "k", "g", "G"}.issubset(keys)


def test_vim_preset_bindings_are_Binding_instances() -> None:
    assert all(isinstance(b, Binding) for b in VIM_PRESET)


def test_resolve_preset_vim_returns_vim_preset() -> None:
    assert resolve_preset("vim") == VIM_PRESET


def test_resolve_preset_is_case_insensitive() -> None:
    assert resolve_preset("VIM") == VIM_PRESET
    assert resolve_preset(" Vim ") == VIM_PRESET


def test_resolve_preset_none_is_empty() -> None:
    assert resolve_preset(None) == ()


def test_resolve_preset_empty_string_is_empty() -> None:
    assert resolve_preset("") == ()


def test_resolve_preset_unknown_is_empty() -> None:
    assert resolve_preset("emacs") == ()


def test_vim_bindings_are_not_priority() -> None:
    """Priority would steal keys from Input widgets mid-typing."""
    assert all(b.priority is False for b in VIM_PRESET)


def test_active_preset_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGUSTA_KEYBINDINGS", "vim")
    assert active_preset_from_env() == VIM_PRESET


def test_active_preset_absent_env_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGUSTA_KEYBINDINGS", raising=False)
    assert active_preset_from_env() == ()


@pytest.mark.asyncio
async def test_app_registers_vim_bindings_when_env_set(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When LANGUSTA_KEYBINDINGS=vim, the app exposes 'j' as a registered key."""
    from langusta.db.migrate import migrate
    from langusta.tui.app import LangustaApp

    home = tmp_path / "home"
    home.mkdir()
    (home / ".langusta").mkdir()
    migrate(home / ".langusta" / "db.sqlite")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LANGUSTA_KEYBINDINGS", "vim")

    async with LangustaApp().run_test() as pilot:
        await pilot.pause()
        keys = {
            k for k in pilot.app._bindings.key_to_bindings
        }
        assert {"j", "k", "g", "G"}.issubset(keys)


@pytest.mark.asyncio
async def test_app_no_vim_bindings_when_env_absent(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no env var set, j/k/g/G are NOT registered on the app bindings."""
    from langusta.db.migrate import migrate
    from langusta.tui.app import LangustaApp

    home = tmp_path / "home"
    home.mkdir()
    (home / ".langusta").mkdir()
    migrate(home / ".langusta" / "db.sqlite")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("LANGUSTA_KEYBINDINGS", raising=False)

    async with LangustaApp().run_test() as pilot:
        await pilot.pause()
        keys = {
            k for k in pilot.app._bindings.key_to_bindings
        }
        # Default app only binds 'q'.
        assert "j" not in keys
        assert "k" not in keys
