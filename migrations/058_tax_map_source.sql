-- migrations/058_tax_map_source.sql
--
-- Slice Y1: a sidewalk's tax-map frontage from DCP's MapPLUTO, cached on the property, and where each page's title-block
-- tax_map_width_ft came from.
--   properties: tax_map_frontage_ft (MapPLUTO's LotFront, above 0 and below 1000), tax_map_source and
--     tax_map_fetched_at, all three set or all three NULL (no fetch yet, or none that worked).
--   pir_annotations: tax_map_source and tax_map_fetched_at beside tax_map_width_ft. A width always has a source; a
--     'manual' source may have no width (someone cleared it, and a refresh leaves it so).
--   tax_map_source is mappluto_<DCP's version> (mappluto_26v2, mappluto_25v1.1), manual, pip_scrape (Y1.5) or r1k.
--
-- Every width already in pir_annotations was typed by hand: it is marked manual, fetched now. Modifies one row in prod
-- today (16 live page-1 title blocks, one with a width). Run before deploying the Slice Y1 code, and deploy straight
-- after: until then the old code's write of a width (title-block editor, annotations PUT, Smart PIR import) carries no
-- source and is refused by chk_pir_annotations_tax_map_provenance. Safe to run twice.
--
-- Rollback (after rolling the code back):
--   ALTER TABLE icid.pir_annotations DROP CONSTRAINT chk_pir_annotations_tax_map_source,
--       DROP CONSTRAINT chk_pir_annotations_tax_map_provenance, DROP COLUMN tax_map_fetched_at, DROP COLUMN tax_map_source;
--   ALTER TABLE icid.properties DROP CONSTRAINT chk_properties_tax_map_source,
--       DROP CONSTRAINT chk_properties_tax_map_frontage, DROP CONSTRAINT chk_properties_tax_map_cache,
--       DROP COLUMN tax_map_fetched_at, DROP COLUMN tax_map_source, DROP COLUMN tax_map_frontage_ft;

BEGIN;

ALTER TABLE icid.properties
    ADD COLUMN IF NOT EXISTS tax_map_frontage_ft NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS tax_map_source TEXT,
    ADD COLUMN IF NOT EXISTS tax_map_fetched_at TIMESTAMPTZ;

ALTER TABLE icid.pir_annotations
    ADD COLUMN IF NOT EXISTS tax_map_source TEXT,
    ADD COLUMN IF NOT EXISTS tax_map_fetched_at TIMESTAMPTZ;

UPDATE icid.pir_annotations
   SET tax_map_source = 'manual', tax_map_fetched_at = now()
 WHERE tax_map_width_ft IS NOT NULL AND tax_map_source IS NULL;

ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_tax_map_source;
ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_tax_map_frontage;
ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_tax_map_cache;
ALTER TABLE icid.properties
    ADD CONSTRAINT chk_properties_tax_map_source
        CHECK (tax_map_source IS NULL OR tax_map_source ~ '^(mappluto_\d{2}v\d+(\.\d+)?|manual|pip_scrape|r1k)$'),
    ADD CONSTRAINT chk_properties_tax_map_frontage
        CHECK (tax_map_frontage_ft IS NULL OR (tax_map_frontage_ft > 0 AND tax_map_frontage_ft < 1000)),
    ADD CONSTRAINT chk_properties_tax_map_cache
        CHECK ((tax_map_frontage_ft IS NULL) = (tax_map_source IS NULL)
               AND (tax_map_source IS NULL) = (tax_map_fetched_at IS NULL));

ALTER TABLE icid.pir_annotations DROP CONSTRAINT IF EXISTS chk_pir_annotations_tax_map_source;
ALTER TABLE icid.pir_annotations DROP CONSTRAINT IF EXISTS chk_pir_annotations_tax_map_provenance;
ALTER TABLE icid.pir_annotations
    ADD CONSTRAINT chk_pir_annotations_tax_map_source
        CHECK (tax_map_source IS NULL OR tax_map_source ~ '^(mappluto_\d{2}v\d+(\.\d+)?|manual|pip_scrape|r1k)$'),
    ADD CONSTRAINT chk_pir_annotations_tax_map_provenance
        CHECK ((tax_map_width_ft IS NULL OR tax_map_source IS NOT NULL)
               AND (tax_map_source IS NULL) = (tax_map_fetched_at IS NULL));

COMMIT;
