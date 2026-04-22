"""Master-password lifecycle tests.

Setup → verify → unlock. Stored in the `meta` table: a salt and a
"verifier" envelope (ciphertext of a known marker). Subsequent unlocks
decrypt the verifier to confirm the password, then return a Vault that
can decrypt `credentials` rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from langusta.crypto.master_password import (
    WrongMasterPassword,
    is_set,
    setup,
    unlock,
)
from langusta.db.connection import connect
from langusta.db.migrate import migrate

NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "mp.sqlite"
    migrate(p)
    return p


def test_is_set_false_on_fresh_db(db: Path) -> None:
    with connect(db) as conn:
        assert is_set(conn) is False


def test_setup_then_is_set_true(db: Path) -> None:
    with connect(db) as conn:
        setup(conn, password="master-password-abc", now=NOW, _for_tests=True)
        assert is_set(conn) is True


def test_setup_twice_raises(db: Path) -> None:
    with connect(db) as conn:
        setup(conn, password="master-password-abc", now=NOW, _for_tests=True)
        with pytest.raises(RuntimeError, match="already"):
            setup(conn, password="different-again-long", now=NOW, _for_tests=True)


def test_unlock_with_correct_password_returns_vault(db: Path) -> None:
    with connect(db) as conn:
        setup(conn, password="master-password-abc", now=NOW, _for_tests=True)
        vault = unlock(conn, password="master-password-abc", _for_tests=True)
    envelope = vault.encrypt(b"probe")
    assert vault.decrypt(envelope) == b"probe"


def test_unlock_with_wrong_password_raises_wrong_master(db: Path) -> None:
    with connect(db) as conn:
        setup(conn, password="master-password-abc", now=NOW, _for_tests=True)
        with pytest.raises(WrongMasterPassword):
            unlock(conn, password="wrong-password-nope", _for_tests=True)


def test_unlock_before_setup_raises(db: Path) -> None:
    with connect(db) as conn, pytest.raises(RuntimeError, match="not set"):
        unlock(conn, password="anything-long-enough", _for_tests=True)


def test_vault_across_setup_unlock_is_functionally_equivalent(db: Path) -> None:
    """setup() and unlock() derive the same key, so encrypt-then-decrypt
    across them must roundtrip."""
    with connect(db) as conn:
        v_setup = setup(conn, password="match-password-foo", now=NOW, _for_tests=True)
        envelope = v_setup.encrypt(b"data")
    with connect(db) as conn:
        v_unlock = unlock(conn, password="match-password-foo", _for_tests=True)
    assert v_unlock.decrypt(envelope) == b"data"


# ---------------------------------------------------------------------------
# Wave-3 TEST-T-013 — unlock rejects tampered-but-decryptable verifier
# ---------------------------------------------------------------------------


def test_unlock_rejects_tampered_verifier_that_decrypts(db: Path) -> None:
    """If an attacker with DB write access replaces the stored verifier
    with their own envelope (encrypting something-other-than-the-
    expected marker under the same vault), `unlock()` must still refuse.
    Covers the `marker != _VERIFIER_PLAINTEXT` branch — the "decrypts
    cleanly but marker mismatches" path the second
    `WrongMasterPassword("... marker mismatch")` exists for."""
    import base64

    from langusta.crypto import master_password as mp
    from langusta.db import meta as meta_dal

    with connect(db) as conn:
        vault = setup(
            conn, password="legit-password-xxxxxxxxxx",
            now=NOW, _for_tests=True,
        )

        # Valid envelope over a DIFFERENT plaintext using the same vault:
        # decrypts cleanly but the marker check must catch it.
        tampered = vault.encrypt(b"NOT-THE-EXPECTED-VERIFIER-MARKER")
        meta_dal.set_value(
            conn,
            mp._VERIFIER_KEY,
            base64.b64encode(tampered.nonce).decode("ascii")
            + ":"
            + base64.b64encode(tampered.ciphertext).decode("ascii"),
            now=NOW,
        )

    with connect(db) as conn, pytest.raises(
        WrongMasterPassword, match="marker",
    ):
        unlock(conn, password="legit-password-xxxxxxxxxx", _for_tests=True)
