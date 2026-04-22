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
import sqlite3

from langusta.crypto.vault import Vault
from langusta.db import credentials as cred_dal
from langusta.db.credentials import CredentialInfo
from langusta.scan.snmp.auth import SnmpAuth, SnmpV2cAuth, SnmpV3Auth


class SnmpCredentialError(ValueError):
    """The named SNMP credential is missing or the wrong kind."""


def resolve_snmp_credential(
    conn: sqlite3.Connection,
    *,
    label: str | None,
    vault: Vault | None,
) -> tuple[SnmpAuth | None, CredentialInfo | None]:
    """Resolve a stored SNMP credential by label.

    Returns `(None, None)` when `label` is None — "the caller didn't
    ask for SNMP enrichment". Raises `SnmpCredentialError` when the
    label doesn't exist or names a non-SNMP credential; raises
    `ValueError` on a malformed stored secret (via `cred_to_snmp_auth`).
    Requires an unlocked `vault` whenever `label` is set.
    """
    if label is None:
        return None, None
    if vault is None:
        raise SnmpCredentialError(
            f"vault is locked; cannot decrypt credential {label!r}"
        )
    info = cred_dal.get_by_label(conn, label)
    if info is None:
        raise SnmpCredentialError(f"no credential with label {label!r}")
    if info.kind not in {"snmp_v2c", "snmp_v3"}:
        raise SnmpCredentialError(
            f"credential {label!r} is {info.kind}, "
            "expected snmp_v2c or snmp_v3"
        )
    secret = cred_dal.get_secret(conn, credential_id=info.id, vault=vault)
    return cred_to_snmp_auth(info, secret), info


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
