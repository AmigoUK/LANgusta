-- Defence-in-depth: partial UNIQUE index on assets.primary_ip so that
-- concurrent asset-creating operations (two scans, or scan + manual add)
-- cannot create duplicate assets at the same IP via a TOCTOU race in the
-- identity resolver.
--
-- Partial (WHERE primary_ip IS NOT NULL) so assets without an IP don't
-- conflict. Existing duplicate IPs (if any) are de-duplicated by keeping
-- the lowest-id asset and rewriting the rest's primary_ip to NULL before
-- applying the constraint.
--
-- Audit D-3.

-- Step 1: null-out primary_ip on duplicate rows (keep lowest id per IP).
UPDATE assets SET primary_ip = NULL
WHERE primary_ip IS NOT NULL
  AND id NOT IN (
      SELECT MIN(id) FROM assets
      WHERE primary_ip IS NOT NULL
      GROUP BY primary_ip
  );

-- Step 2: drop the non-unique index and recreate as unique partial.
DROP INDEX IF EXISTS idx_assets_primary_ip;
CREATE UNIQUE INDEX idx_assets_primary_ip_unique
    ON assets(primary_ip) WHERE primary_ip IS NOT NULL;
