-- FTS5 search index over asset text fields (M4).
--
-- Triggers keep `assets_fts` in sync with `assets` — insert, update, delete.
-- MAC search uses a direct LIKE on `mac_addresses` (1-to-many, not worth
-- duplicating into the FTS index).

-- Standalone FTS5 table (duplicates the indexed columns; acceptable at
-- v1's ≤250-asset scale). Triggers keep it in sync.

CREATE VIRTUAL TABLE assets_fts USING fts5(
    hostname, primary_ip, description, vendor, location, owner, detected_os, device_type,
    tokenize='porter unicode61'
);


CREATE TRIGGER assets_fts_ai AFTER INSERT ON assets BEGIN
    INSERT INTO assets_fts (
        rowid, hostname, primary_ip, description, vendor, location, owner,
        detected_os, device_type
    ) VALUES (
        new.id,
        coalesce(new.hostname, ''),
        coalesce(new.primary_ip, ''),
        coalesce(new.description, ''),
        coalesce(new.vendor, ''),
        coalesce(new.location, ''),
        coalesce(new.owner, ''),
        coalesce(new.detected_os, ''),
        coalesce(new.device_type, '')
    );
END;


CREATE TRIGGER assets_fts_au AFTER UPDATE ON assets BEGIN
    UPDATE assets_fts SET
        hostname    = coalesce(new.hostname, ''),
        primary_ip  = coalesce(new.primary_ip, ''),
        description = coalesce(new.description, ''),
        vendor      = coalesce(new.vendor, ''),
        location    = coalesce(new.location, ''),
        owner       = coalesce(new.owner, ''),
        detected_os = coalesce(new.detected_os, ''),
        device_type = coalesce(new.device_type, '')
    WHERE rowid = old.id;
END;


CREATE TRIGGER assets_fts_ad AFTER DELETE ON assets BEGIN
    DELETE FROM assets_fts WHERE rowid = old.id;
END;
