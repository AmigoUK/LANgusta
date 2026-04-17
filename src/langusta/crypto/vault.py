"""Encrypted credential vault.

AES-256-GCM via the `cryptography` library, with keys derived from the
master password via Argon2id. Each encryption uses a fresh 12-byte nonce;
authenticated encryption detects tampering of ciphertext OR nonce.

Spec: docs/specs/02-tech-stack-and-architecture.md §8, §16.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from langusta.crypto.kdf import DEFAULT_PARAMS, TEST_PARAMS, Argon2Params, derive_key, generate_salt

_MIN_PASSWORD_CHARS = 12
_NONCE_BYTES = 12


class InvalidPassword(Exception):  # noqa: N818 — domain error name
    """Decryption failed — wrong password, tampering, or a mismatched salt."""


@dataclass(frozen=True, slots=True)
class Envelope:
    """Opaque container: nonce + ciphertext+auth-tag. Safe to persist."""

    nonce: bytes
    ciphertext: bytes

    def replace(self, **kwargs) -> Envelope:
        return replace(self, **kwargs)


class Vault:
    """Holds an in-memory derived key for encrypting/decrypting secrets.

    Callers construct via `Vault.unlock(password, salt, params)` — use
    `Vault.for_tests(...)` for unit tests (cheaper KDF).
    """

    def __init__(self, key: bytes, salt: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        self._aead = AESGCM(key)
        self._salt = salt

    @classmethod
    def unlock(
        cls,
        *,
        password: str,
        salt: bytes,
        params: Argon2Params = DEFAULT_PARAMS,
    ) -> Vault:
        """Unlock the vault with an existing salt (read from `meta`)."""
        if len(password) < _MIN_PASSWORD_CHARS:
            raise ValueError(f"master password must be at least {_MIN_PASSWORD_CHARS} chars")
        key = derive_key(password, salt, params)
        return cls(key=key, salt=salt)

    @classmethod
    def for_tests(
        cls,
        *,
        password: str,
        salt: bytes | None = None,
    ) -> Vault:
        """Build a Vault with fast KDF params — tests only."""
        if len(password) < _MIN_PASSWORD_CHARS:
            raise ValueError(f"master password must be at least {_MIN_PASSWORD_CHARS} chars")
        salt = salt if salt is not None else generate_salt()
        key = derive_key(password, salt, TEST_PARAMS)
        return cls(key=key, salt=salt)

    @property
    def salt(self) -> bytes:
        return self._salt

    def encrypt(self, plaintext: bytes) -> Envelope:
        nonce = os.urandom(_NONCE_BYTES)
        ct = self._aead.encrypt(nonce, plaintext, associated_data=None)
        return Envelope(nonce=nonce, ciphertext=ct)

    def decrypt(self, envelope: Envelope) -> bytes:
        try:
            return self._aead.decrypt(envelope.nonce, envelope.ciphertext, associated_data=None)
        except InvalidTag as exc:
            raise InvalidPassword(
                "unable to decrypt envelope: wrong password, tampering, or mismatched salt"
            ) from exc
