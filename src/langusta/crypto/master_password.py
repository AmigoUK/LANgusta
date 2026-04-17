"""Master-password lifecycle — setup, verify, unlock.

State lives in the `meta` table under two keys:
  - vault_salt: base64-encoded Argon2id salt
  - vault_verifier: base64(nonce + ciphertext) of a known marker string

Setup: generate salt, derive key, encrypt the marker, persist both.
Unlock: load salt, derive key, decrypt the marker to confirm — return
the Vault to the caller.

Wrong password surfaces as `WrongMasterPassword`, not a stack trace.
"""

from __future__ import annotations

import base64
import sqlite3
from datetime import datetime

from langusta.crypto.kdf import DEFAULT_PARAMS, TEST_PARAMS, Argon2Params, generate_salt
from langusta.crypto.vault import Envelope, InvalidPassword, Vault
from langusta.db import meta as meta_dal

_SALT_KEY = "vault_salt"
_VERIFIER_KEY = "vault_verifier"
_VERIFIER_PLAINTEXT = b"LANGUSTA-MASTER-PASSWORD-VERIFIER-v1"


class WrongMasterPassword(Exception):  # noqa: N818 — domain error
    """Decrypting the verifier failed — the user typed the wrong password."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _params(for_tests: bool) -> Argon2Params:
    return TEST_PARAMS if for_tests else DEFAULT_PARAMS


def is_set(conn: sqlite3.Connection) -> bool:
    return meta_dal.get(conn, _SALT_KEY) is not None and meta_dal.get(conn, _VERIFIER_KEY) is not None


def setup(
    conn: sqlite3.Connection,
    *,
    password: str,
    now: datetime,
    _for_tests: bool = False,
) -> Vault:
    """Initialise the master password. Raises if already set."""
    if is_set(conn):
        raise RuntimeError("master password already set")
    salt = generate_salt()
    vault = Vault.unlock(password=password, salt=salt, params=_params(_for_tests))
    verifier = vault.encrypt(_VERIFIER_PLAINTEXT)
    meta_dal.set_value(conn, _SALT_KEY, _b64(salt), now=now)
    meta_dal.set_value(
        conn, _VERIFIER_KEY,
        _b64(verifier.nonce) + ":" + _b64(verifier.ciphertext),
        now=now,
    )
    return vault


def unlock(
    conn: sqlite3.Connection,
    *,
    password: str,
    _for_tests: bool = False,
) -> Vault:
    """Derive the key and verify against the stored marker. Returns the Vault."""
    salt_b64 = meta_dal.get(conn, _SALT_KEY)
    ver_raw = meta_dal.get(conn, _VERIFIER_KEY)
    if salt_b64 is None or ver_raw is None:
        raise RuntimeError("master password not set — run `langusta init` first")
    nonce_b64, ct_b64 = ver_raw.split(":", 1)
    envelope = Envelope(nonce=_unb64(nonce_b64), ciphertext=_unb64(ct_b64))
    vault = Vault.unlock(password=password, salt=_unb64(salt_b64), params=_params(_for_tests))
    try:
        marker = vault.decrypt(envelope)
    except InvalidPassword as exc:
        raise WrongMasterPassword("master password is incorrect") from exc
    if marker != _VERIFIER_PLAINTEXT:
        raise WrongMasterPassword("master password is incorrect (marker mismatch)")
    return vault
