"""Credentials DAL.

Wraps the `credentials` table. The only way to retrieve a plaintext secret
is via `get_secret(conn, credential_id, vault=...)`. `list_info` returns
only metadata — label, kind, created_at — never ciphertext.

Spec: docs/specs/02-tech-stack-and-architecture.md §8.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from langusta.crypto.vault import Envelope, Vault

VALID_KINDS = frozenset({"snmp_v2c", "snmp_v3", "ssh_key", "ssh_password", "api_token"})


class DuplicateLabel(ValueError):  # noqa: N818 — domain error
    """A credential with the given label already exists."""


@dataclass(frozen=True, slots=True)
class CredentialInfo:
    id: int
    label: str
    kind: str
    created_at: datetime


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def create(
    conn: sqlite3.Connection,
    *,
    label: str,
    kind: str,
    secret: bytes,
    vault: Vault,
    now: datetime,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown credential kind {kind!r}; valid: {sorted(VALID_KINDS)}")

    envelope = vault.encrypt(secret)
    try:
        row = conn.execute(
            "INSERT INTO credentials (label, kind, nonce, ciphertext, created_at) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (label, kind, envelope.nonce, envelope.ciphertext, _iso(now)),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: credentials.label" in str(exc):
            raise DuplicateLabel(f"credential label {label!r} already exists") from exc
        raise
    return int(row[0])


def list_info(conn: sqlite3.Connection) -> list[CredentialInfo]:
    rows = conn.execute(
        "SELECT id, label, kind, created_at FROM credentials ORDER BY id",
    ).fetchall()
    return [
        CredentialInfo(
            id=int(r["id"]),
            label=r["label"],
            kind=r["kind"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


def get_by_label(conn: sqlite3.Connection, label: str) -> CredentialInfo | None:
    row = conn.execute(
        "SELECT id, label, kind, created_at FROM credentials WHERE label = ?",
        (label,),
    ).fetchone()
    if row is None:
        return None
    return CredentialInfo(
        id=int(row["id"]),
        label=row["label"],
        kind=row["kind"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def get_by_id(conn: sqlite3.Connection, credential_id: int) -> CredentialInfo | None:
    row = conn.execute(
        "SELECT id, label, kind, created_at FROM credentials WHERE id = ?",
        (credential_id,),
    ).fetchone()
    if row is None:
        return None
    return CredentialInfo(
        id=int(row["id"]),
        label=row["label"],
        kind=row["kind"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def get_secret(
    conn: sqlite3.Connection,
    *,
    credential_id: int,
    vault: Vault,
) -> bytes:
    row = conn.execute(
        "SELECT nonce, ciphertext FROM credentials WHERE id = ?",
        (credential_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"credential id={credential_id} not found")
    envelope = Envelope(nonce=bytes(row["nonce"]), ciphertext=bytes(row["ciphertext"]))
    return vault.decrypt(envelope)


def delete(conn: sqlite3.Connection, *, credential_id: int) -> None:
    conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
