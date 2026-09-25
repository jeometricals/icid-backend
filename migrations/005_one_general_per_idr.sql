-- migrations/005_one_general_per_idr.sql
-- Phase R, Slice R2: At most one General (GEN) report per IDR.
--
-- Adds a partial UNIQUE index on icid.idr_reports(idr_id) covering only
-- non-addendum GEN rows. General is the IDR's summary/coversheet (R4's
-- auto-summary target), so an IDR may hold at most one. Every other
-- (idr_id, report_type, is_addendum) combination stays unrestricted:
-- other report types may repeat within a day, and addenda always can.
--
-- Run in Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Index should not exist yet:
--   SELECT indexname FROM pg_indexes
--   WHERE schemaname = 'icid' AND indexname = 'uq_idr_reports_one_gen_per_idr';
--   Expected: 0 rows

-- 2. No IDR already holds more than one non-addendum GEN report
--    (the index cannot be created over existing duplicates):
--   SELECT idr_id, COUNT(*)
--   FROM icid.idr_reports
--   WHERE report_type = 'GEN' AND is_addendum = false
--   GROUP BY idr_id
--   HAVING COUNT(*) > 1;
--   Expected: 0 rows

-- 3. Baseline GEN count:
--   SELECT COUNT(*) FROM icid.idr_reports
--   WHERE report_type = 'GEN' AND is_addendum = false;
--   Expected: 3 (one per migrated IDR)

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE UNIQUE INDEX uq_idr_reports_one_gen_per_idr
    ON icid.idr_reports(idr_id)
    WHERE report_type = 'GEN' AND is_addendum = false;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Index exists, is unique, and carries the partial predicate:
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE schemaname = 'icid' AND indexname = 'uq_idr_reports_one_gen_per_idr';
--   Expected: 1 row; indexdef contains "UNIQUE" and
--   "WHERE ((report_type = 'GEN'::text) AND (is_addendum = false))"

-- B. A second GEN in the same IDR is rejected:
--   BEGIN;
--   INSERT INTO icid.idr_reports (idr_id, report_type)
--   SELECT idr_id, 'GEN' FROM icid.idr_reports
--   WHERE report_type = 'GEN' LIMIT 1;
--   ROLLBACK;
--   Expected: ERROR duplicate key value violates unique constraint
--   "uq_idr_reports_one_gen_per_idr"

-- C. A non-GEN type still repeats freely in the same IDR:
--   BEGIN;
--   INSERT INTO icid.idr_reports (idr_id, report_type)
--   SELECT idr_id, 'SWR' FROM icid.idr_reports WHERE report_type = 'GEN' LIMIT 1;
--   INSERT INTO icid.idr_reports (idr_id, report_type)
--   SELECT idr_id, 'SWR' FROM icid.idr_reports WHERE report_type = 'GEN' LIMIT 1;
--   ROLLBACK;
--   Expected: both inserts succeed (INSERT 0 1 twice), then rolled back

-- D. Row counts unchanged:
--   SELECT COUNT(*) FROM icid.idr_reports;
--   Expected: same as before the migration (3)
