-- Monitoring subsystem (M7).
--
-- Spec: docs/specs/01-functionality-and-moscow.md §4 Pillar C;
--       docs/specs/02-tech-stack-and-architecture.md §7.
--
-- Each `monitoring_checks` row is a subscription: run kind <kind> against
-- asset <asset_id> every <interval_seconds>. `last_status` is a coarse
-- 'ok'|'fail'|NULL flag used to detect ok↔fail transitions — the runner
-- writes a 'monitor_event' timeline entry whenever state flips.

CREATE TABLE monitoring_checks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id          INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    kind              TEXT    NOT NULL
        CHECK (kind IN ('icmp', 'tcp', 'http')),
    target            TEXT,                       -- overrides asset.primary_ip when set
    port              INTEGER,                    -- for tcp + http
    path              TEXT,                       -- for http; defaults to '/'
    interval_seconds  INTEGER NOT NULL
        CHECK (interval_seconds >= 10),
    enabled           INTEGER NOT NULL DEFAULT 1
        CHECK (enabled IN (0, 1)),
    created_at        TEXT    NOT NULL,
    last_run_at       TEXT,                       -- NULL until first run
    last_status       TEXT
        CHECK (last_status IS NULL OR last_status IN ('ok', 'fail'))
);

CREATE INDEX idx_monitoring_enabled ON monitoring_checks(enabled, last_run_at);
CREATE INDEX idx_monitoring_asset ON monitoring_checks(asset_id);


CREATE TABLE check_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id        INTEGER NOT NULL REFERENCES monitoring_checks(id) ON DELETE CASCADE,
    asset_id        INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    status          TEXT    NOT NULL
        CHECK (status IN ('ok', 'fail')),
    latency_ms      REAL,                         -- NULL for failures / not measured
    detail          TEXT,                         -- human-readable context
    recorded_at     TEXT    NOT NULL
);

CREATE INDEX idx_check_results_asset_time ON check_results(asset_id, recorded_at);
CREATE INDEX idx_check_results_check ON check_results(check_id, recorded_at);
