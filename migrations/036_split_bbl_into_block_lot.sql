-- migrations/036_split_bbl_into_block_lot.sql
--
-- Replaces icid.properties.bbl with first-class block and lot columns.
--
-- NEW COLUMNS (both nullable; required for sidewalks via properties_kind_fields_consistent):
--   block  — TEXT, no leading zeros (display form: "123", "0" when zero). CHECK digits-only.
--   lot    — TEXT, no leading zeros (display form: "45", "0" when zero). CHECK digits-only.
--
-- DROPPED:
--   bbl column
--   uq_properties_project_bbl index
--
-- NEW UNIQUE:
--   uq_properties_project_sidewalk_bbl  ON (project_id, boro, block, lot) WHERE kind='sidewalk' AND deleted_at IS NULL
--
-- CHECK:
--   properties_kind_fields_consistent updated: sidewalks need block + lot + address + property_type; ped ramps forbid them.
--
-- Rollback: adding bbl back and backfilling from boro + block + lot is deterministic; the old unique index would be recreated.
--
-- Precheck (read-only, run before this migration): every sidewalk, deleted or not, has a 10-digit BBL starting 1-5,
-- and no two live sidewalks of a project share a borough, block and lot:
--   SELECT COUNT(*) FILTER (WHERE kind = 'sidewalk' AND (bbl IS NULL OR bbl !~ '^[1-5][0-9]{9}$')) AS unparseable
--   FROM icid.properties;
-- Must be 0.

BEGIN;

-- 1. Add the new columns.
ALTER TABLE icid.properties ADD COLUMN block TEXT;
ALTER TABLE icid.properties ADD COLUMN lot TEXT;

-- 2. Backfill from existing BBLs for every sidewalk (deleted ones too: the CHECK below holds them as well).
--    Matches the display-form convention of R1e.2's _bbl_part helper: strip leading zeros, "0" when all zeros.
UPDATE icid.properties
SET block = CASE WHEN substring(bbl FROM 2 FOR 5) = '00000' THEN '0'
                 ELSE ltrim(substring(bbl FROM 2 FOR 5), '0') END,
    lot   = CASE WHEN substring(bbl FROM 7 FOR 4) = '0000'  THEN '0'
                 ELSE ltrim(substring(bbl FROM 7 FOR 4), '0') END
WHERE kind = 'sidewalk'
  AND bbl IS NOT NULL;

-- 3. Digit-only CHECKs.
ALTER TABLE icid.properties ADD CONSTRAINT chk_properties_block_format CHECK (
  block IS NULL OR block ~ '^(0|[1-9][0-9]{0,4})$'
);
ALTER TABLE icid.properties ADD CONSTRAINT chk_properties_lot_format CHECK (
  lot IS NULL OR lot ~ '^(0|[1-9][0-9]{0,3})$'
);

-- 4. Drop the old BBL-based unique index.
DROP INDEX IF EXISTS icid.uq_properties_project_bbl;

-- 5. Replace the kind-fields CHECK with one that doesn't reference bbl.
ALTER TABLE icid.properties DROP CONSTRAINT properties_kind_fields_consistent;

ALTER TABLE icid.properties ADD CONSTRAINT properties_kind_fields_consistent CHECK (
  (kind = 'sidewalk' AND
   block IS NOT NULL AND
   lot IS NOT NULL AND
   address IS NOT NULL AND
   property_type IS NOT NULL AND
   intersection_street_1 IS NULL AND
   intersection_street_2 IS NULL AND
   quadrant IS NULL AND
   facing_street IS NULL AND
   ramp_type IS NULL)
  OR
  (kind = 'ped_ramp' AND
   block IS NULL AND
   lot IS NULL AND
   address IS NULL AND
   property_type IS NULL AND
   violation_no IS NULL AND
   intersection_street_1 IS NOT NULL AND
   intersection_street_2 IS NOT NULL AND
   quadrant IS NOT NULL AND
   facing_street IS NOT NULL AND
   ramp_type IS NOT NULL)
);

-- 6. Drop the BBL column.
ALTER TABLE icid.properties DROP COLUMN bbl;

-- 7. New partial unique: a sidewalk is uniquely a (boro, block, lot) within a project.
CREATE UNIQUE INDEX uq_properties_project_sidewalk_bbl
  ON icid.properties (project_id, boro, block, lot)
  WHERE kind = 'sidewalk' AND deleted_at IS NULL;

COMMIT;
