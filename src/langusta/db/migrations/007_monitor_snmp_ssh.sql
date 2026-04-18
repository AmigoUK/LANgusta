-- Extend monitoring_checks for SNMP-OID and SSH-command check kinds (0.2.0).
--
-- SQLite cannot ALTER a CHECK constraint in place, so we rebuild the table.
-- All existing rows are copied verbatim and the new columns default to NULL.
-- Per ADR-0005 a pre-migration backup is written automatically; and the
-- restore-from-old-backup test in tests/unit/db/test_migrate.py covers the
-- forward path.

CREATE TABLE monitoring_checks_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id          INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind              TEXT    NOT NULL
        CHECK (kind IN ('icmp', 'tcp', 'http', 'snmp_oid', 'ssh_command')),
    target            TEXT,
    port              INTEGER,
    path              TEXT,
    interval_seconds  INTEGER NOT NULL
        CHECK (interval_seconds >= 10),
    enabled           INTEGER NOT NULL DEFAULT 1
        CHECK (enabled IN (0, 1)),
    created_at        TEXT    NOT NULL,
    last_run_at       TEXT,
    last_status       TEXT
        CHECK (last_status IS NULL OR last_status IN ('ok', 'fail')),
    -- new fields for SNMP-OID + SSH-command check kinds
    oid               TEXT,
    expected_value    TEXT,
    comparator        TEXT
        CHECK (comparator IS NULL OR comparator IN ('eq', 'neq', 'contains', 'gt', 'lt')),
    command           TEXT,
    success_exit_code INTEGER,
    stdout_pattern    TEXT,
    timeout_seconds   REAL,
    credential_id     INTEGER REFERENCES credentials(id) ON DELETE RESTRICT,
    username          TEXT
);

INSERT INTO monitoring_checks_new (
    id, asset_id, kind, target, port, path, interval_seconds,
    enabled, created_at, last_run_at, last_status
)
SELECT
    id, asset_id, kind, target, port, path, interval_seconds,
    enabled, created_at, last_run_at, last_status
FROM monitoring_checks;

DROP TABLE monitoring_checks;
ALTER TABLE monitoring_checks_new RENAME TO monitoring_checks;

CREATE INDEX idx_monitoring_enabled ON monitoring_checks(enabled, last_run_at);
CREATE INDEX idx_monitoring_asset ON monitoring_checks(asset_id);
