-- Encrypted credential storage (M5).
--
-- The ciphertext column holds an AES-256-GCM envelope (nonce + ct+tag).
-- The key is derived per-process from the master password; the DB stores
-- the salt in `meta` under 'vault_salt' so the same password yields the
-- same key on next startup.

CREATE TABLE credentials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL
        CHECK (kind IN ('snmp_v2c', 'snmp_v3', 'ssh_key', 'ssh_password', 'api_token')),
    nonce       BLOB    NOT NULL,
    ciphertext  BLOB    NOT NULL,
    created_at  TEXT    NOT NULL
);
