"""Argon2id key derivation.

Spec: docs/specs/02-tech-stack-and-architecture.md §8, §16.

Defaults target ~500ms on modern hardware (spec §16). Tests override with
lighter params so the suite stays fast.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw


@dataclass(frozen=True, slots=True)
class Argon2Params:
    time_cost: int
    memory_cost: int   # KiB
    parallelism: int
    key_len: int

    def __post_init__(self) -> None:
        if self.time_cost < 2:
            raise ValueError("time_cost < 2 is too weak")
        if self.memory_cost < 32 * 1024:
            raise ValueError("memory_cost < 32 MiB is too weak")
        if self.key_len < 16:
            raise ValueError("key_len < 16 is too weak")


# Tuned for modern hardware — ~500ms. Users with slow boxes can tune down
# via the config file (M6); starting conservative is the right default.
DEFAULT_PARAMS = Argon2Params(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    key_len=32,
)


# Fast parameters for tests; still meets the minimum floor enforced by
# Argon2Params.
TEST_PARAMS = Argon2Params(
    time_cost=2,
    memory_cost=32 * 1024,  # 32 MiB
    parallelism=1,
    key_len=32,
)


def generate_salt(num_bytes: int = 16) -> bytes:
    """Return a fresh random salt for KDF input."""
    return os.urandom(num_bytes)


def derive_key(password: str, salt: bytes, params: Argon2Params = DEFAULT_PARAMS) -> bytes:
    """Derive `params.key_len` bytes from a password + salt via Argon2id."""
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=params.key_len,
        type=Type.ID,
    )
