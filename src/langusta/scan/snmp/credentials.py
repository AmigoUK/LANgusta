"""Decode vault secrets into `SnmpAuth` values.

The vault stores opaque bytes. For SNMP credentials the encoding depends
on kind:

- `snmp_v2c` — UTF-8 community string (unchanged from M5).
- `snmp_v3` — JSON object with keys `username`, `auth_protocol`,
  `auth_passphrase`, `priv_protocol`, `priv_passphrase`. Passphrases may
  be `null` when the corresponding protocol is `"NONE"`.
"""

from __future__ import annotations

import json

from langusta.db.credentials import CredentialInfo
from langusta.scan.snmp.auth import SnmpAuth, SnmpV2cAuth, SnmpV3Auth


def cred_to_snmp_auth(info: CredentialInfo, secret: bytes) -> SnmpAuth:
    if info.kind == "snmp_v2c":
        return SnmpV2cAuth(community=secret.decode("utf-8"))
    if info.kind == "snmp_v3":
        payload = json.loads(secret.decode("utf-8"))
        return SnmpV3Auth(
            username=payload["username"],
            auth_protocol=payload["auth_protocol"],
            auth_passphrase=payload.get("auth_passphrase"),
            priv_protocol=payload["priv_protocol"],
            priv_passphrase=payload.get("priv_passphrase"),
        )
    raise ValueError(
        f"credential kind {info.kind!r} is not an SNMP credential "
        "(expected 'snmp_v2c' or 'snmp_v3')"
    )


def encode_snmp_v3_secret(
    *,
    username: str,
    auth_protocol: str,
    auth_passphrase: str | None,
    priv_protocol: str,
    priv_passphrase: str | None,
) -> bytes:
    """Validate + serialise v3 parameters for storage in the vault."""
    # Validate by constructing the dataclass (raises on bad input).
    SnmpV3Auth(
        username=username,
        auth_protocol=auth_protocol,
        auth_passphrase=auth_passphrase,
        priv_protocol=priv_protocol,
        priv_passphrase=priv_passphrase,
    )
    return json.dumps(
        {
            "username": username,
            "auth_protocol": auth_protocol,
            "auth_passphrase": auth_passphrase,
            "priv_protocol": priv_protocol,
            "priv_passphrase": priv_passphrase,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
