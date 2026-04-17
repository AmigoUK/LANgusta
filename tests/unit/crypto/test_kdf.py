"""KDF parameter and behaviour tests."""

from __future__ import annotations

from langusta.crypto.kdf import (
    DEFAULT_PARAMS,
    Argon2Params,
    derive_key,
    generate_salt,
)


def test_default_params_meet_security_floor() -> None:
    """Spec §16: Argon2id tuned to ~500ms on modern hardware. These numbers
    aren't load-dependent in tests; we just check they're sane."""
    assert DEFAULT_PARAMS.time_cost >= 2
    assert DEFAULT_PARAMS.memory_cost >= 32 * 1024  # 32 MiB minimum
    assert DEFAULT_PARAMS.parallelism >= 1
    assert DEFAULT_PARAMS.key_len == 32  # AES-256


def test_generate_salt_is_random_and_16_bytes() -> None:
    a = generate_salt()
    b = generate_salt()
    assert len(a) == 16
    assert len(b) == 16
    assert a != b


def test_derive_key_is_deterministic_for_same_inputs() -> None:
    params = Argon2Params(time_cost=2, memory_cost=32 * 1024, parallelism=1, key_len=32)
    salt = b"static-salt-16-b"
    k1 = derive_key("my-password-abc123", salt, params)
    k2 = derive_key("my-password-abc123", salt, params)
    assert k1 == k2
    assert len(k1) == 32


def test_derive_key_differs_with_different_salt() -> None:
    params = Argon2Params(time_cost=2, memory_cost=32 * 1024, parallelism=1, key_len=32)
    k1 = derive_key("pw-one-password", b"salt-salt-salt-a", params)
    k2 = derive_key("pw-one-password", b"salt-salt-salt-b", params)
    assert k1 != k2


def test_derive_key_differs_with_different_password() -> None:
    params = Argon2Params(time_cost=2, memory_cost=32 * 1024, parallelism=1, key_len=32)
    k1 = derive_key("pw-one-the-first", b"salt-static-16-b", params)
    k2 = derive_key("pw-two-the-second", b"salt-static-16-b", params)
    assert k1 != k2
