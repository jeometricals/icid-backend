-- migrations/065_tax_map_depth.sql
--
-- Pilot sweep #2 (#5a): a sidewalk's lot depth from DCP's MapPLUTO (LotDepth), cached beside its frontage
-- (tax_map_frontage_ft, Slice Y1) and fetched with it, for the Construction Report's "Tax Map: W' x D'" block.
--   properties.tax_map_depth_ft: above 0 and below 1000 (a suspect LotDepth is left out, as a frontage is), and only
--   beside a frontage: it shares the frontage's tax_map_source and tax_map_fetched_at. A frontage may have no depth
--   (fetched before this, or MapPLUTO's depth is suspect).
--
-- Schema only; nullable, so the code before it runs unchanged. Run before deploying the sweep's code (which reads and
-- writes the column), then fill it with `python scripts/backfill_mappluto.py --depth` (a dry run; --commit writes).
-- Safe to run twice.
--
-- Rollback (after rolling the code back):
--   ALTER TABLE icid.properties DROP CONSTRAINT chk_properties_tax_map_depth, DROP COLUMN tax_map_depth_ft;

BEGIN;

ALTER TABLE icid.properties ADD COLUMN IF NOT EXISTS tax_map_depth_ft NUMERIC(10,2);

ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_tax_map_depth;
ALTER TABLE icid.properties ADD CONSTRAINT chk_properties_tax_map_depth
    CHECK (tax_map_depth_ft IS NULL
           OR (tax_map_depth_ft > 0 AND tax_map_depth_ft < 1000 AND tax_map_frontage_ft IS NOT NULL));

COMMIT;
