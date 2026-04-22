"""`langusta.paths` tests — env handling + canonical locations.

Wave-3 T-008 (test-gap) + S-013 (security). `langusta_home()` reads
`$LANGUSTA_HOME` and falls back to `~/.langusta`; this module pins the
happy paths and the S-013 guard against relative-path overrides.
"""

from __future__ import annotations

import pytest

from langusta import paths


def test_langusta_home_defaults_to_home_dot_langusta(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("LANGUSTA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.langusta_home() == tmp_path / ".langusta"


def test_langusta_home_honours_absolute_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    override = tmp_path / "custom"
    monkeypatch.setenv("LANGUSTA_HOME", str(override))
    assert paths.langusta_home() == override


def test_langusta_home_rejects_relative_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave-3 S-013. A relative `LANGUSTA_HOME` resolves to the CWD,
    which almost never matches the user's intent and can land the DB
    in an unexpected directory. Refuse up front."""
    monkeypatch.setenv("LANGUSTA_HOME", "./relative/langusta")
    with pytest.raises(ValueError, match="absolute"):
        paths.langusta_home()


def test_langusta_home_rejects_dot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGUSTA_HOME", ".")
    with pytest.raises(ValueError, match="absolute"):
        paths.langusta_home()


def test_langusta_home_ignores_empty_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """An empty `LANGUSTA_HOME=''` must fall back to the default
    rather than error — it's a common shape for 'env var cleared'."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LANGUSTA_HOME", "")
    assert paths.langusta_home() == tmp_path / ".langusta"


def test_all_canonical_paths_live_under_langusta_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Every path helper must compose off `langusta_home()`; a rogue
    `~/.langusta/` literal would slip a file past the env redirect."""
    monkeypatch.setenv("LANGUSTA_HOME", str(tmp_path / "root"))
    home = paths.langusta_home()
    for fn in (
        paths.db_path,
        paths.backups_dir,
        paths.config_path,
        paths.known_hosts_path,
        paths.monitor_pid_path,
        paths.monitor_log_path,
        paths.notifications_log_path,
    ):
        result = fn()
        assert str(result).startswith(str(home)), (
            f"{fn.__name__}() returned {result!r} which escapes "
            f"langusta_home() = {home!r}"
        )
