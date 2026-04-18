"""SSH auth type + credential decoding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from langusta.db.credentials import CredentialInfo
from langusta.monitor.ssh.auth import (
    SshKeyAuth,
    SshPasswordAuth,
    cred_to_ssh_auth,
)


def _info(kind: str) -> CredentialInfo:
    return CredentialInfo(id=1, label="x", kind=kind, created_at=datetime.now(UTC))


def test_cred_to_ssh_auth_ssh_key() -> None:
    auth = cred_to_ssh_auth(_info("ssh_key"), b"-----BEGIN OPENSSH PRIVATE KEY-----\nXYZ\n")
    assert isinstance(auth, SshKeyAuth)
    assert auth.private_key_pem.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert auth.passphrase is None


def test_cred_to_ssh_auth_ssh_password() -> None:
    auth = cred_to_ssh_auth(_info("ssh_password"), b"hunter2")
    assert isinstance(auth, SshPasswordAuth)
    assert auth.password == "hunter2"


def test_cred_to_ssh_auth_rejects_snmp_kind() -> None:
    with pytest.raises(ValueError, match="not an SSH credential"):
        cred_to_ssh_auth(_info("snmp_v2c"), b"public")
