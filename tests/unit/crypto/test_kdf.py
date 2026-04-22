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


# ---------------------------------------------------------------------------
# Wave-3 TEST-T-014 — floor-violation rejections
# ---------------------------------------------------------------------------


def test_argon2_params_rejects_time_cost_below_floor() -> None:
    """Wave-3 T-014. Spec §16 names a security floor; the
    Argon2Params constructor enforces it, but the rejection path had
    no direct coverage. A config-file tweak that slipped below the
    floor would otherwise be accepted silently on future reads."""
    import pytest

    with pytest.raises(ValueError, match="time_cost"):
        Argon2Params(
            time_cost=1, memory_cost=32 * 1024, parallelism=1, key_len=32,
        )
    with pytest.raises(ValueError, match="time_cost"):
        Argon2Params(
            time_cost=0, memory_cost=32 * 1024, parallelism=1, key_len=32,
        )


def test_argon2_params_rejects_memory_cost_below_32_mib() -> None:
    import pytest

    with pytest.raises(ValueError, match="memory_cost"):
        Argon2Params(
            time_cost=2, memory_cost=16 * 1024, parallelism=1, key_len=32,
        )
    with pytest.raises(ValueError, match="memory_cost"):
        Argon2Params(
            time_cost=2, memory_cost=0, parallelism=1, key_len=32,
        )


def test_argon2_params_rejects_key_len_below_16_bytes() -> None:
    import pytest

    with pytest.raises(ValueError, match="key_len"):
        Argon2Params(
            time_cost=2, memory_cost=32 * 1024, parallelism=1, key_len=8,
        )
    with pytest.raises(ValueError, match="key_len"):
        Argon2Params(
            time_cost=2, memory_cost=32 * 1024, parallelism=1, key_len=0,
        )


def test_argon2_params_accepts_minimum_acceptable_values() -> None:
    """Boundary: exactly-at-the-floor values must construct without
    error — the rejection messages are strict less-than."""
    p = Argon2Params(
        time_cost=2, memory_cost=32 * 1024, parallelism=1, key_len=16,
    )
    assert p.time_cost == 2
    assert p.memory_cost == 32 * 1024
    assert p.key_len == 16
