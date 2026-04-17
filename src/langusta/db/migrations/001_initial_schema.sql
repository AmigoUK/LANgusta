-- LANgusta initial schema (v1 foundations).
--
-- Spec: docs/specs/02-tech-stack-and-architecture.md §3, §10.
-- ADRs: 0001 (raw sqlite3 + thin DAL), 0005 (forward-only migrations).
--
-- Load-bearing rules encoded here:
--   1. timeline_entries is append-only (BEFORE UPDATE / BEFORE DELETE triggers).
--   2. field_provenance tracks who last set each asset field (scanner-
--      proposes-human-disposes invariant; see core/provenance.py).
--   3. Every row has ISO-8601 UTC timestamps as TEXT — SQLite has no native
--      datetime; standardising on the string form avoids timezone drift.
--
-- Tables added here serve milestones M0-M2. Later migrations add credentials
-- (M5), monitoring (M7), FTS5 search index (M3), export metadata (M6).


-- --------------------------------------------------------------------------
-- Migration tracking
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS _migrations (
    id           INTEGER PRIMARY KEY,
    description  TEXT    NOT NULL,
    checksum     TEXT    NOT NULL,
    applied_at   TEXT    NOT NULL       -- ISO-8601 UTC
);


-- --------------------------------------------------------------------------
-- Instance metadata (key/value, single-row per key)
-- --------------------------------------------------------------------------

CREATE TABLE meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL,
    set_at  TEXT NOT NULL
);


-- --------------------------------------------------------------------------
-- Assets — the core table
-- --------------------------------------------------------------------------

CREATE TABLE assets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname          TEXT,
    primary_ip        TEXT,
    vendor            TEXT,
    detected_os       TEXT,
    device_type       TEXT,
    description       TEXT,
    location          TEXT,
    owner             TEXT,
    management_url    TEXT,
    criticality       TEXT,
    first_seen        TEXT NOT NULL,      -- ISO-8601 UTC
    last_seen         TEXT NOT NULL,
    source            TEXT NOT NULL       -- 'scanned' | 'manual' | 'imported'
        CHECK (source IN ('scanned', 'manual', 'imported'))
);

CREATE INDEX idx_assets_primary_ip ON assets(primary_ip);
CREATE INDEX idx_assets_hostname   ON assets(hostname);
CREATE INDEX idx_assets_last_seen  ON assets(last_seen);


-- --------------------------------------------------------------------------
-- MAC addresses — one asset may carry many (dual-NIC, bond, virtual)
-- --------------------------------------------------------------------------

CREATE TABLE mac_addresses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    mac        TEXT    NOT NULL,          -- normalised lowercase aa:bb:cc:dd:ee:ff
    first_seen TEXT    NOT NULL,
    last_seen  TEXT    NOT NULL,
    UNIQUE (mac)
);

CREATE INDEX idx_mac_asset ON mac_addresses(asset_id);


-- --------------------------------------------------------------------------
-- Field provenance — who last set each field of each asset
-- --------------------------------------------------------------------------

CREATE TABLE field_provenance (
    asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    field       TEXT    NOT NULL,
    provenance  TEXT    NOT NULL
        CHECK (provenance IN ('scanned', 'manual', 'imported')),
    set_at      TEXT    NOT NULL,
    PRIMARY KEY (asset_id, field)
);


-- --------------------------------------------------------------------------
-- Scans — each run of `langusta scan` gets a row here
-- --------------------------------------------------------------------------

CREATE TABLE scans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    target       TEXT    NOT NULL,       -- CIDR, single IP, or 'auto'
    started_at   TEXT    NOT NULL,
    finished_at  TEXT,                    -- NULL until completed
    host_count   INTEGER,                 -- populated on completion
    note         TEXT
);


-- --------------------------------------------------------------------------
-- Proposed changes — scanner observations that would modify a protected
-- field (MANUAL / IMPORTED provenance). Reviewed by the human via M4 UX.
-- --------------------------------------------------------------------------

CREATE TABLE proposed_changes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id             INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    field                TEXT    NOT NULL,
    current_value        TEXT,
    current_provenance   TEXT    NOT NULL
        CHECK (current_provenance IN ('manual', 'imported')),
    proposed_value       TEXT,
    observed_at          TEXT    NOT NULL,
    scan_id              INTEGER REFERENCES scans(id) ON DELETE SET NULL,
    resolution           TEXT
        CHECK (resolution IS NULL OR resolution IN ('accepted', 'rejected', 'edited')),
    resolved_at          TEXT,
    resolved_override    TEXT                 -- value the user chose if resolution='edited'
);

CREATE INDEX idx_proposed_open ON proposed_changes(asset_id) WHERE resolution IS NULL;


-- --------------------------------------------------------------------------
-- Review queue — ambiguous identity matches, not simple field conflicts
-- --------------------------------------------------------------------------

CREATE TABLE review_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id      INTEGER REFERENCES scans(id) ON DELETE SET NULL,
    observed_at  TEXT    NOT NULL,
    observation  TEXT    NOT NULL,          -- JSON-encoded candidate asset
    candidates   TEXT    NOT NULL,          -- JSON list of possible asset_id matches with scores
    resolution   TEXT
        CHECK (resolution IS NULL OR resolution IN ('merged', 'new_asset', 'discarded')),
    resolved_at  TEXT,
    resolved_to  INTEGER REFERENCES assets(id) ON DELETE SET NULL
);

CREATE INDEX idx_review_open ON review_queue(observed_at) WHERE resolution IS NULL;


-- --------------------------------------------------------------------------
-- Timeline entries — APPEND-ONLY. Product promise: institutional memory.
-- --------------------------------------------------------------------------

CREATE TABLE timeline_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind          TEXT    NOT NULL
        CHECK (kind IN (
            'note',            -- manual journal entry
            'scan_diff',       -- scanner detected a change
            'monitor_event',   -- monitoring check result (failure/recovery)
            'disposition',     -- review queue decision recorded
            'correction',      -- new entry superseding an earlier one
            'import',          -- imported from external tool
            'system'           -- internal event (e.g., 'asset created')
        )),
    body          TEXT    NOT NULL,         -- markdown-capable
    occurred_at   TEXT    NOT NULL,         -- ISO-8601 UTC
    corrects_id   INTEGER REFERENCES timeline_entries(id) ON DELETE SET NULL,
    author        TEXT                      -- 'system', 'scanner', 'monitor', or username
);

CREATE INDEX idx_timeline_asset_time ON timeline_entries(asset_id, occurred_at);


-- Enforce immutability at the storage layer. Anyone who bypasses the DAL and
-- opens the raw .sqlite file with `sqlite3` will still hit these triggers.

CREATE TRIGGER timeline_entries_no_update
BEFORE UPDATE ON timeline_entries
BEGIN
    SELECT RAISE(ABORT, 'timeline_entries are immutable; insert a correction entry instead');
END;

CREATE TRIGGER timeline_entries_no_delete
BEFORE DELETE ON timeline_entries
BEGIN
    SELECT RAISE(ABORT, 'timeline_entries are immutable; deletion is not permitted');
END;
