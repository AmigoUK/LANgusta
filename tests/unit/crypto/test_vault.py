"""Credential vault tests (AES-256-GCM + Argon2id).

Spec: docs/specs/02-tech-stack-and-architecture.md §8 (credential storage),
      §16 (security defaults).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from langusta.crypto.vault import InvalidPassword, Vault


@pytest.fixture
def vault() -> Vault:
    # Fast parameters for tests — production uses higher KDF costs.
    return Vault.for_tests(password="hunter2-correctly-long-enough")


def test_encrypt_decrypt_roundtrip(vault: Vault) -> None:
    plaintext = b"public-community"
    envelope = vault.encrypt(plaintext)
    assert vault.decrypt(envelope) == plaintext


def test_wrong_password_raises_invalid_password() -> None:
    v1 = Vault.for_tests(password="correct-horse-battery-staple")
    envelope = v1.encrypt(b"secret")
    v2 = Vault.for_tests(password="wrong-password-still-long-ish", salt=v1.salt)
    with pytest.raises(InvalidPassword):
        v2.decrypt(envelope)


def test_tampered_ciphertext_fails_decryption(vault: Vault) -> None:
    envelope = vault.encrypt(b"secret")
    # Flip one bit of the ciphertext (first byte).
    tampered = envelope.replace(
        ciphertext=bytes([envelope.ciphertext[0] ^ 0x01]) + envelope.ciphertext[1:],
    )
    with pytest.raises(InvalidPassword):
        vault.decrypt(tampered)


def test_tampered_nonce_fails_decryption(vault: Vault) -> None:
    envelope = vault.encrypt(b"secret")
    tampered = envelope.replace(
        nonce=bytes([envelope.nonce[0] ^ 0x01]) + envelope.nonce[1:],
    )
    with pytest.raises(InvalidPassword):
        vault.decrypt(tampered)


def test_each_encrypt_uses_new_nonce(vault: Vault) -> None:
    """Nonce reuse with the same key is catastrophic — test it doesn't happen."""
    e1 = vault.encrypt(b"secret")
    e2 = vault.encrypt(b"secret")
    assert e1.nonce != e2.nonce
    # Ciphertexts differ too (since nonce differs).
    assert e1.ciphertext != e2.ciphertext


def test_password_too_short_rejected() -> None:
    """Spec §16: min 12 chars."""
    with pytest.raises(ValueError, match="12"):
        Vault.for_tests(password="short")


@given(
    plaintext=st.binary(min_size=0, max_size=256),
)
def test_property_any_plaintext_roundtrips(plaintext: bytes) -> None:
    v = Vault.for_tests(password="strong-master-password-ok")
    assert v.decrypt(v.encrypt(plaintext)) == plaintext


def test_vault_remembers_salt_for_derivation() -> None:
    """If the same password + salt is provided, two Vault instances can
    decrypt each other's output."""
    v1 = Vault.for_tests(password="master-pw-here-long-enough")
    envelope = v1.encrypt(b"secret")
    v2 = Vault.for_tests(password="master-pw-here-long-enough", salt=v1.salt)
    assert v2.decrypt(envelope) == b"secret"
