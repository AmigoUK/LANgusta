"""Credentials DAL tests — store encrypted, retrieve via vault, list safely.

The `list_info` call MUST NOT expose ciphertext or plaintext — only label
and kind. Decrypting the secret requires the master password via the Vault.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.crypto.vault import InvalidPassword, Vault
from langusta.db import credentials as cred_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "cred.sqlite"
    migrate(p)
    return p


@pytest.fixture
def vault() -> Vault:
    return Vault.for_tests(password="master-password-ok")


def test_create_returns_id(db: Path, vault: Vault) -> None:
    with connect(db) as conn:
        cred_id = cred_dal.create(
            conn, label="office-ro", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
    assert isinstance(cred_id, int) and cred_id > 0


def test_get_secret_roundtrips_plaintext(db: Path, vault: Vault) -> None:
    with connect(db) as conn:
        cred_id = cred_dal.create(
            conn, label="office-ro", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
        secret = cred_dal.get_secret(conn, credential_id=cred_id, vault=vault)
    assert secret == b"public"


def test_list_info_exposes_only_label_kind_and_timestamp(db: Path, vault: Vault) -> None:
    with connect(db) as conn:
        cred_dal.create(
            conn, label="office-ro", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
        info_rows = cred_dal.list_info(conn)
    assert len(info_rows) == 1
    info = info_rows[0]
    assert info.label == "office-ro"
    assert info.kind == "snmp_v2c"
    assert info.created_at == NOW
    # No plaintext / ciphertext attributes at all.
    assert not hasattr(info, "secret")
    assert not hasattr(info, "ciphertext")
    assert not hasattr(info, "nonce")


def test_duplicate_label_rejected(db: Path, vault: Vault) -> None:
    with connect(db) as conn:
        cred_dal.create(conn, label="a", kind="snmp_v2c", secret=b"x", vault=vault, now=NOW)
        with pytest.raises(cred_dal.DuplicateLabel):
            cred_dal.create(conn, label="a", kind="snmp_v2c", secret=b"y", vault=vault, now=NOW)


def test_wrong_password_vault_fails_decryption(db: Path) -> None:
    v1 = Vault.for_tests(password="correct-master-password")
    with connect(db) as conn:
        cred_id = cred_dal.create(
            conn, label="x", kind="snmp_v2c", secret=b"s", vault=v1, now=NOW,
        )
    v2 = Vault.for_tests(password="totally-wrong-password", salt=v1.salt)
    with connect(db) as conn, pytest.raises(InvalidPassword):
        cred_dal.get_secret(conn, credential_id=cred_id, vault=v2)


def test_get_by_label_returns_id(db: Path, vault: Vault) -> None:
    with connect(db) as conn:
        cred_id = cred_dal.create(
            conn, label="office-ro", kind="snmp_v2c", secret=b"s", vault=vault, now=NOW,
        )
        found = cred_dal.get_by_label(conn, "office-ro")
    assert found is not None
    assert found.id == cred_id
    assert found.kind == "snmp_v2c"


def test_get_by_label_unknown_returns_none(db: Path) -> None:
    with connect(db) as conn:
        assert cred_dal.get_by_label(conn, "nope") is None


def test_delete_removes_row(db: Path, vault: Vault) -> None:
    with connect(db) as conn:
        cred_id = cred_dal.create(
            conn, label="drop", kind="snmp_v2c", secret=b"x", vault=vault, now=NOW,
        )
        cred_dal.delete(conn, credential_id=cred_id)
        assert cred_dal.list_info(conn) == []


def test_delete_unknown_id_is_noop(db: Path) -> None:
    with connect(db) as conn:
        cred_dal.delete(conn, credential_id=999)  # must not raise


def test_create_rejects_invalid_kind(db: Path, vault: Vault) -> None:
    with connect(db) as conn, pytest.raises(ValueError, match="unknown"):
        cred_dal.create(
            conn, label="bad", kind="nope", secret=b"x", vault=vault, now=NOW,
        )
