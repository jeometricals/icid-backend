-- migrations/035_property_subtyping.sql
--
-- Adds the sidewalk vs ped-ramp subtype to icid.properties.
--
-- NEW COLUMNS:
--   kind                  — enum (sidewalk | ped_ramp), NOT NULL, default sidewalk
--   boro                  — enum (Manhattan | Bronx | Brooklyn | Queens | Staten Island), NOT NULL;
--                           backfilled for sidewalks from BBL's first digit
--   violation_no          — TEXT nullable (sidewalk-only field)
--   intersection_street_1 — TEXT nullable (ped-ramp only)
--   intersection_street_2 — TEXT nullable (ped-ramp only)
--   quadrant              — enum (NE | NW | SE | SW), nullable (ped-ramp only)
--   facing_street         — enum (street_1 | street_2), nullable (ped-ramp only)
--   ramp_type             — enum (PERPENDICULAR | PARALLEL | CUT_THROUGH), nullable (ped-ramp only)
--
-- RELAXED NOT NULL:
--   bbl, address          — ped ramps do not carry these
--   property_type         — ped ramps do not carry residential classification
--
-- Rollback: dropping NOT NULL on bbl and address is reversible only if every row has values
-- in those columns before NOT NULL is re-applied. Delete or backfill ped-ramp rows before rolling back.
--
-- Precheck (read-only, run before this migration): every property, deleted or not, has a BBL and an
-- address, and every BBL starts with 1-5 (the backfill and SET NOT NULL touch every row):
--   SELECT COUNT(*) FILTER (WHERE bbl IS NULL OR address IS NULL)  AS missing_core,
--          COUNT(*) FILTER (WHERE bbl !~ '^[1-5][0-9]{9}$')        AS not_ten_digits
--   FROM icid.properties;
-- Both must be 0.

BEGIN;

-- 1. kind enum + column (backfills existing rows via DEFAULT)
CREATE TYPE icid.property_kind AS ENUM ('sidewalk', 'ped_ramp');

ALTER TABLE icid.properties
  ADD COLUMN kind icid.property_kind NOT NULL DEFAULT 'sidewalk';

-- 2. boro enum + column, backfill sidewalks from BBL first digit
CREATE TYPE icid.boro AS ENUM ('Manhattan', 'Bronx', 'Brooklyn', 'Queens', 'Staten Island');

ALTER TABLE icid.properties ADD COLUMN boro icid.boro;

UPDATE icid.properties
SET boro = CASE SUBSTRING(bbl FROM 1 FOR 1)
  WHEN '1' THEN 'Manhattan'::icid.boro
  WHEN '2' THEN 'Bronx'::icid.boro
  WHEN '3' THEN 'Brooklyn'::icid.boro
  WHEN '4' THEN 'Queens'::icid.boro
  WHEN '5' THEN 'Staten Island'::icid.boro
END
WHERE bbl IS NOT NULL;

ALTER TABLE icid.properties ALTER COLUMN boro SET NOT NULL;

-- 3. Loosen sidewalk-only NOT NULLs
ALTER TABLE icid.properties ALTER COLUMN bbl DROP NOT NULL;
ALTER TABLE icid.properties ALTER COLUMN address DROP NOT NULL;
ALTER TABLE icid.properties ALTER COLUMN property_type DROP NOT NULL;

-- 4. Violation No.
ALTER TABLE icid.properties ADD COLUMN violation_no TEXT;

-- 5. Ped-ramp columns
ALTER TABLE icid.properties ADD COLUMN intersection_street_1 TEXT;
ALTER TABLE icid.properties ADD COLUMN intersection_street_2 TEXT;

CREATE TYPE icid.quadrant AS ENUM ('NE', 'NW', 'SE', 'SW');
ALTER TABLE icid.properties ADD COLUMN quadrant icid.quadrant;

CREATE TYPE icid.facing_street AS ENUM ('street_1', 'street_2');
ALTER TABLE icid.properties ADD COLUMN facing_street icid.facing_street;

CREATE TYPE icid.ramp_type AS ENUM ('PERPENDICULAR', 'PARALLEL', 'CUT_THROUGH');
ALTER TABLE icid.properties ADD COLUMN ramp_type icid.ramp_type;

-- 6. CHECK: kind-field consistency
ALTER TABLE icid.properties ADD CONSTRAINT properties_kind_fields_consistent CHECK (
  (kind = 'sidewalk' AND
   bbl IS NOT NULL AND
   address IS NOT NULL AND
   property_type IS NOT NULL AND
   intersection_street_1 IS NULL AND
   intersection_street_2 IS NULL AND
   quadrant IS NULL AND
   facing_street IS NULL AND
   ramp_type IS NULL)
  OR
  (kind = 'ped_ramp' AND
   bbl IS NULL AND
   address IS NULL AND
   property_type IS NULL AND
   violation_no IS NULL AND
   intersection_street_1 IS NOT NULL AND
   intersection_street_2 IS NOT NULL AND
   quadrant IS NOT NULL AND
   facing_street IS NOT NULL AND
   ramp_type IS NOT NULL)
);

COMMIT;
