"""SnmpAuth value-type validation + credential decoding."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from langusta.db.credentials import CredentialInfo
from langusta.scan.snmp.auth import SnmpV2cAuth, SnmpV3Auth
from langusta.scan.snmp.credentials import cred_to_snmp_auth, encode_snmp_v3_secret


def _info(kind: str) -> CredentialInfo:
    return CredentialInfo(id=1, label="lab", kind=kind, created_at=datetime.now(UTC))


def test_v3_rejects_unknown_auth_protocol() -> None:
    with pytest.raises(ValueError, match="auth_protocol"):
        SnmpV3Auth(
            username="admin",
            auth_protocol="SHA-3",  # not in ALLOWED_AUTH
            auth_passphrase="x",
            priv_protocol="AES-128",
            priv_passphrase="y",
        )


def test_v3_rejects_unknown_priv_protocol() -> None:
    with pytest.raises(ValueError, match="priv_protocol"):
        SnmpV3Auth(
            username="admin",
            auth_protocol="SHA",
            auth_passphrase="x",
            priv_protocol="RC4",  # not in ALLOWED_PRIV
            priv_passphrase="y",
        )


def test_v3_rejects_missing_auth_passphrase_when_required() -> None:
    with pytest.raises(ValueError, match="auth_passphrase is required"):
        SnmpV3Auth(
            username="admin",
            auth_protocol="SHA",
            auth_passphrase=None,
            priv_protocol="NONE",
            priv_passphrase=None,
        )


def test_v3_rejects_priv_without_auth() -> None:
    """noAuthPriv is forbidden by USM: priv requires auth."""
    with pytest.raises(ValueError, match="priv requires auth"):
        SnmpV3Auth(
            username="admin",
            auth_protocol="NONE",
            auth_passphrase=None,
            priv_protocol="AES-128",
            priv_passphrase="x",
        )


def test_v3_accepts_auth_only_no_priv() -> None:
    """authNoPriv is a valid USM security level."""
    auth = SnmpV3Auth(
        username="admin",
        auth_protocol="SHA",
        auth_passphrase="authpass",
        priv_protocol="NONE",
        priv_passphrase=None,
    )
    assert auth.priv_protocol == "NONE"


def test_cred_to_snmp_auth_v2c_decodes_utf8_community() -> None:
    auth = cred_to_snmp_auth(_info("snmp_v2c"), b"public")
    assert isinstance(auth, SnmpV2cAuth)
    assert auth.community == "public"


def test_cred_to_snmp_auth_v3_decodes_json() -> None:
    secret = encode_snmp_v3_secret(
        username="admin",
        auth_protocol="SHA",
        auth_passphrase="authpass",
        priv_protocol="AES-128",
        priv_passphrase="privpass",
    )
    auth = cred_to_snmp_auth(_info("snmp_v3"), secret)
    assert isinstance(auth, SnmpV3Auth)
    assert auth.username == "admin"
    assert auth.auth_protocol == "SHA"
    assert auth.priv_passphrase == "privpass"


def test_cred_to_snmp_auth_wrong_kind_raises() -> None:
    with pytest.raises(ValueError, match="not an SNMP credential"):
        cred_to_snmp_auth(_info("ssh_key"), b"...")


def test_encode_snmp_v3_secret_validates_before_serialising() -> None:
    with pytest.raises(ValueError, match="priv_protocol"):
        encode_snmp_v3_secret(
            username="admin",
            auth_protocol="SHA",
            auth_passphrase="x",
            priv_protocol="RC4",
            priv_passphrase="y",
        )


def test_encode_snmp_v3_secret_round_trips_through_json() -> None:
    encoded = encode_snmp_v3_secret(
        username="admin",
        auth_protocol="SHA-256",
        auth_passphrase="authpass",
        priv_protocol="AES-256",
        priv_passphrase="privpass",
    )
    payload = json.loads(encoded.decode("utf-8"))
    assert payload["username"] == "admin"
    assert payload["auth_protocol"] == "SHA-256"
    assert payload["priv_protocol"] == "AES-256"


# ---------------------------------------------------------------------------
# Wave-3 S-011 — weak-protocol deprecation warnings
# ---------------------------------------------------------------------------


def test_md5_auth_emits_weak_protocol_warning() -> None:
    from langusta.scan.snmp.auth import WeakSnmpv3ProtocolWarning

    with pytest.warns(WeakSnmpv3ProtocolWarning, match="MD5"):
        SnmpV3Auth(
            username="u", auth_protocol="MD5", auth_passphrase="p",
            priv_protocol="AES-128", priv_passphrase="q",
        )


def test_des_priv_emits_weak_protocol_warning() -> None:
    from langusta.scan.snmp.auth import WeakSnmpv3ProtocolWarning

    with pytest.warns(WeakSnmpv3ProtocolWarning, match="DES"):
        SnmpV3Auth(
            username="u", auth_protocol="SHA", auth_passphrase="p",
            priv_protocol="DES", priv_passphrase="q",
        )


def test_3des_priv_emits_weak_protocol_warning() -> None:
    from langusta.scan.snmp.auth import WeakSnmpv3ProtocolWarning

    with pytest.warns(WeakSnmpv3ProtocolWarning, match="3DES"):
        SnmpV3Auth(
            username="u", auth_protocol="SHA", auth_passphrase="p",
            priv_protocol="3DES", priv_passphrase="q",
        )


def test_modern_sha256_aes128_emits_no_warning() -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # turn any warning into a failure
        SnmpV3Auth(
            username="u", auth_protocol="SHA-256", auth_passphrase="p",
            priv_protocol="AES-128", priv_passphrase="q",
        )
