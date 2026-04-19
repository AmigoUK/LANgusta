"""Unit tests — KnownHostsStore (TOFU store for SSH host keys)."""

from __future__ import annotations

from pathlib import Path

import pytest

from langusta.monitor.ssh.known_hosts import (
    HostKeyEntry,
    KeyMismatchError,
    KeyNotPinnedError,
    KnownHostsStore,
)

ED25519 = "ssh-ed25519"
KEY_A = "AAAAC3NzaC1lZDI1NTE5AAAAICaPpSvNhFO7oxKU6UZ8lRvm7HOwrAAAAAAAAAAAA"
KEY_B = "AAAAC3NzaC1lZDI1NTE5AAAAID1fFerentKeyBytesXXXXXXXXXXXXXXXXXXXXXXXX"


def _store(tmp_path: Path) -> KnownHostsStore:
    return KnownHostsStore(tmp_path / ".langusta" / "known_hosts")


def test_empty_store_exists_is_false(tmp_path: Path) -> None:
    assert _store(tmp_path).exists() is False


def test_add_creates_file_and_parent(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    assert s.exists()
    assert (tmp_path / ".langusta").is_dir()


def test_add_then_contains_true(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    assert s.contains("10.0.0.1", 22) is True


def test_contains_returns_false_for_unseen_host(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    assert s.contains("10.0.0.2", 22) is False


def test_port_is_part_of_identity(tmp_path: Path) -> None:
    """Same host, different port = different pin."""
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    assert s.contains("10.0.0.1", 2222) is False


def test_add_refuses_to_overwrite_existing_pin(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    with pytest.raises(KeyMismatchError):
        s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_B))


def test_verify_accepts_matching_pin(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    s.verify("10.0.0.1", 22, ED25519, KEY_A)


def test_verify_rejects_changed_key(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    with pytest.raises(KeyMismatchError):
        s.verify("10.0.0.1", 22, ED25519, KEY_B)


def test_verify_rejects_changed_key_type(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    with pytest.raises(KeyMismatchError):
        s.verify("10.0.0.1", 22, "ssh-rsa", KEY_A)


def test_verify_raises_when_host_not_pinned(tmp_path: Path) -> None:
    s = _store(tmp_path)
    with pytest.raises(KeyNotPinnedError):
        s.verify("10.0.0.1", 22, ED25519, KEY_A)


def test_openssh_line_default_port_has_no_brackets(tmp_path: Path) -> None:
    entry = HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A)
    line = entry.to_openssh_line().rstrip("\n")
    assert line.split()[0] == "10.0.0.1"


def test_openssh_line_non_default_port_uses_brackets(tmp_path: Path) -> None:
    entry = HostKeyEntry("10.0.0.1", 2222, ED25519, KEY_A)
    line = entry.to_openssh_line().rstrip("\n")
    assert line.split()[0] == "[10.0.0.1]:2222"


def test_entries_parses_file_with_multiple_hosts(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.add(HostKeyEntry("10.0.0.1", 22, ED25519, KEY_A))
    s.add(HostKeyEntry("10.0.0.2", 2222, ED25519, KEY_B))
    entries = s.entries()
    assert len(entries) == 2
    assert entries[0].host == "10.0.0.1" and entries[0].port == 22
    assert entries[1].host == "10.0.0.2" and entries[1].port == 2222


def test_entries_skips_comment_lines(tmp_path: Path) -> None:
    path = tmp_path / ".langusta" / "known_hosts"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# this is a comment\n"
        "10.0.0.1 ssh-ed25519 " + KEY_A + "\n"
        "# another comment\n",
        encoding="utf-8",
    )
    s = KnownHostsStore(path)
    entries = s.entries()
    assert len(entries) == 1
    assert entries[0].host == "10.0.0.1"


def test_entries_tolerates_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / ".langusta" / "known_hosts"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n10.0.0.1 ssh-ed25519 " + KEY_A + "\n\n",
        encoding="utf-8",
    )
    assert len(KnownHostsStore(path).entries()) == 1
