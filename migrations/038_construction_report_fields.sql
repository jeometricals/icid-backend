-- migrations/038_construction_report_fields.sql
--
-- R1h: what the Construction Report (DOT form 5/95) prints that ICID didn't hold yet.
--
-- NEW TYPE:
--   icid.compass_point          — 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'.
--
-- NEW COLUMNS:
--   pirs.north_bearing          — icid.compass_point nullable: which way North is on the PIR's sketch (every page of
--                                 a PIR shows the same site the same way round). NULL: not known; no north arrow.
--   pir_annotations.date_notice_issued — DATE nullable: the day the violation notice was issued.
--   pir_annotations.date_excavated     — DATE nullable: the day the sidewalk was dug out.
--   pir_annotations.date_constructed   — DATE nullable: the day the concrete was poured, as typed on the title
--                                 block. Not flags.constructed_on, which is the claiming IDR's report_date.
--
-- All four are nullable and default to NULL, so every existing row is unchanged and the code before 038 keeps
-- working after it (it never names them). The code after 038 reads pirs.north_bearing on every PIR read: run this
-- first, then deploy.
--
-- Rollback:
--   ALTER TABLE icid.pir_annotations DROP COLUMN date_notice_issued, DROP COLUMN date_excavated,
--     DROP COLUMN date_constructed;
--   ALTER TABLE icid.pirs DROP COLUMN north_bearing;
--   DROP TYPE icid.compass_point;
--
-- Precheck (read-only): none of the new names exists yet.
--   SELECT to_regtype('icid.compass_point');                                                    -- must be NULL
--   SELECT column_name FROM information_schema.columns WHERE table_schema = 'icid'
--     AND column_name IN ('north_bearing', 'date_notice_issued', 'date_excavated', 'date_constructed');  -- no rows

BEGIN;

CREATE TYPE icid.compass_point AS ENUM ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW');

ALTER TABLE icid.pirs ADD COLUMN north_bearing icid.compass_point;

ALTER TABLE icid.pir_annotations
  ADD COLUMN date_notice_issued DATE,
  ADD COLUMN date_excavated     DATE,
  ADD COLUMN date_constructed   DATE;

COMMIT;
