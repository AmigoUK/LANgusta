-- Notification sinks (post-v1).
--
-- Each row is a configured sink: webhook, SMTP, or an opt-in extra log
-- location. The always-on ~/.langusta/notifications.log is NOT represented
-- here — it's handled unconditionally by the dispatch function.

CREATE TABLE notification_sinks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL
        CHECK (kind IN ('webhook', 'smtp', 'logfile')),
    config      TEXT    NOT NULL,   -- JSON
    enabled     INTEGER NOT NULL DEFAULT 1
        CHECK (enabled IN (0, 1)),
    created_at  TEXT    NOT NULL
);

CREATE INDEX idx_notification_sinks_enabled
    ON notification_sinks(enabled);
