"""SNMP authentication value types.

`SnmpAuth` is the sum of v2c (community string) and v3 (USM user with
auth+priv protocols). The `SnmpClient` Protocol takes an `SnmpAuth` so
backends can dispatch on it without leaking pysnmp types to callers.

Protocols are validated on construction — passing an unknown auth or priv
protocol name raises immediately rather than failing later inside pysnmp.
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_AUTH = frozenset({"NONE", "MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"})
ALLOWED_PRIV = frozenset({"NONE", "DES", "3DES", "AES-128", "AES-192", "AES-256"})


@dataclass(frozen=True, slots=True)
class SnmpV2cAuth:
    community: str


@dataclass(frozen=True, slots=True)
class SnmpV3Auth:
    username: str
    auth_protocol: str
    auth_passphrase: str | None
    priv_protocol: str
    priv_passphrase: str | None

    def __post_init__(self) -> None:
        if self.auth_protocol not in ALLOWED_AUTH:
            raise ValueError(
                f"unknown auth_protocol {self.auth_protocol!r}; valid: {sorted(ALLOWED_AUTH)}"
            )
        if self.priv_protocol not in ALLOWED_PRIV:
            raise ValueError(
                f"unknown priv_protocol {self.priv_protocol!r}; valid: {sorted(ALLOWED_PRIV)}"
            )
        if self.auth_protocol != "NONE" and not self.auth_passphrase:
            raise ValueError("auth_passphrase is required when auth_protocol != 'NONE'")
        if self.priv_protocol != "NONE" and not self.priv_passphrase:
            raise ValueError("priv_passphrase is required when priv_protocol != 'NONE'")
        if self.priv_protocol != "NONE" and self.auth_protocol == "NONE":
            raise ValueError("priv requires auth (USM forbids noAuthPriv)")


SnmpAuth = SnmpV2cAuth | SnmpV3Auth
