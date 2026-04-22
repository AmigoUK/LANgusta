"""`resolve_snmp_credential` — unit tests.

Wave-3 TEST-A-010. Extracts the SNMP-credential resolution logic out of
`cli.py`'s `scan` command so the shape is testable in isolation (and
re-usable by any future caller that wants to thread SNMP into a scan).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.crypto.master_password import setup as mp_setup
from langusta.db import credentials as cred_dal
from langusta.db.connection import connect
from langusta.db.migrate import migrate
from langusta.scan.snmp.credentials import (
    SnmpCredentialError,
    resolve_snmp_credential,
)

NOW = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
_PW = "master-pw-for-resolve-snmp-tests-xxxx"


@pytest.fixture
def db_with_vault(tmp_path: Path):
    db = tmp_path / "sn.sqlite"
    migrate(db)
    with connect(db) as conn:
        vault = mp_setup(conn, password=_PW, now=NOW, _for_tests=True)
    return db, vault


def test_returns_none_none_when_label_is_none(db_with_vault) -> None:
    """Caller didn't ask for SNMP enrichment — no vault work required."""
    db, _vault = db_with_vault
    with connect(db) as conn:
        auth, info = resolve_snmp_credential(conn, label=None, vault=None)
    assert auth is None
    assert info is None


def test_raises_snmp_cred_error_when_label_unknown(db_with_vault) -> None:
    db, vault = db_with_vault
    with connect(db) as conn, pytest.raises(
        SnmpCredentialError, match="no credential",
    ):
        resolve_snmp_credential(conn, label="not-there", vault=vault)


def test_raises_snmp_cred_error_on_wrong_kind(db_with_vault) -> None:
    """A label that exists but names a non-SNMP credential (e.g.
    ssh_key) must be rejected — the scan path cannot consume it."""
    db, vault = db_with_vault
    with connect(db) as conn:
        cred_dal.create(
            conn, label="ssh-cred", kind="ssh_password",
            secret=b"hunter2", vault=vault, now=NOW,
        )
    with connect(db) as conn, pytest.raises(
        SnmpCredentialError, match="ssh_password",
    ):
        resolve_snmp_credential(conn, label="ssh-cred", vault=vault)


def test_raises_when_vault_is_locked(db_with_vault) -> None:
    """Caller passed a label but no unlocked vault — can't decrypt the
    secret. Surface as SnmpCredentialError rather than an obscure KeyError."""
    db, vault = db_with_vault
    with connect(db) as conn:
        cred_dal.create(
            conn, label="v2c", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
    with connect(db) as conn, pytest.raises(
        SnmpCredentialError, match="vault is locked",
    ):
        resolve_snmp_credential(conn, label="v2c", vault=None)


def test_returns_snmp_v2c_auth_on_happy_path(db_with_vault) -> None:
    from langusta.scan.snmp.auth import SnmpV2cAuth

    db, vault = db_with_vault
    with connect(db) as conn:
        cred_dal.create(
            conn, label="v2c", kind="snmp_v2c",
            secret=b"public", vault=vault, now=NOW,
        )
    with connect(db) as conn:
        auth, info = resolve_snmp_credential(conn, label="v2c", vault=vault)
    assert isinstance(auth, SnmpV2cAuth)
    assert auth.community == "public"
    assert info is not None and info.kind == "snmp_v2c"


def test_returns_snmp_v3_auth_on_happy_path(db_with_vault) -> None:
    from langusta.scan.snmp.auth import SnmpV3Auth

    db, vault = db_with_vault
    v3_secret = json.dumps({
        "username": "admin",
        "auth_protocol": "SHA",
        "auth_passphrase": "authpass",
        "priv_protocol": "AES-128",
        "priv_passphrase": "privpass",
    }).encode("utf-8")

    with connect(db) as conn:
        cred_dal.create(
            conn, label="v3", kind="snmp_v3",
            secret=v3_secret, vault=vault, now=NOW,
        )
    with connect(db) as conn:
        auth, info = resolve_snmp_credential(conn, label="v3", vault=vault)
    assert isinstance(auth, SnmpV3Auth)
    assert auth.username == "admin"
    assert info is not None and info.kind == "snmp_v3"
