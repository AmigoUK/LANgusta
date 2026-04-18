"""SSH authentication value types + credential decoding.

Mirrors `scan/snmp/auth.py`: immutable dataclasses, protocol-level validation
happens at construction. Two kinds are supported in v0.2.0:

- `SshKeyAuth(private_key_pem, passphrase)` — corresponds to credential
  kind `ssh_key`. Secret is the UTF-8 private-key PEM.
- `SshPasswordAuth(password)` — corresponds to credential kind
  `ssh_password`. Secret is the UTF-8 password bytes.

Agent-forwarding, jump hosts, and known-host pinning are deliberately
out of scope for v1 — see the plan's "out of scope" section.
"""

from __future__ import annotations

from dataclasses import dataclass

from langusta.db.credentials import CredentialInfo


@dataclass(frozen=True, slots=True)
class SshKeyAuth:
    private_key_pem: str
    passphrase: str | None = None


@dataclass(frozen=True, slots=True)
class SshPasswordAuth:
    password: str


SshAuth = SshKeyAuth | SshPasswordAuth


def cred_to_ssh_auth(info: CredentialInfo, secret: bytes) -> SshAuth:
    if info.kind == "ssh_key":
        return SshKeyAuth(private_key_pem=secret.decode("utf-8"))
    if info.kind == "ssh_password":
        return SshPasswordAuth(password=secret.decode("utf-8"))
    raise ValueError(
        f"credential kind {info.kind!r} is not an SSH credential "
        "(expected 'ssh_key' or 'ssh_password')"
    )
