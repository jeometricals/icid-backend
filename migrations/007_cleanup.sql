-- migrations/007_cleanup.sql
-- Phase R, Slice R5: final cleanup.
--   1. Backfill the Slice 5 IDR (submitted before R3 page numbering):
--      icid.idrs.total_pages = 1, and page_number = 1 on its one report.
--   2. DROP icid.completed_forms and icid.reports (pre-IDR model; data was
--      migrated into idrs/idr_reports by 004).
--
-- Run in the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The Slice 5 IDR is submitted and has no total_pages:
--   SELECT idr_id, status, total_pages FROM icid.idrs
--   WHERE idr_id = '8b2f887b-eff9-4ef6-a11b-03bb7d977cf3';
--   Expected: 1 row; status submitted, total_pages NULL

-- 2. It holds exactly one report, unnumbered:
--   SELECT report_id, report_type, is_addendum, page_number FROM icid.idr_reports
--   WHERE idr_id = '8b2f887b-eff9-4ef6-a11b-03bb7d977cf3';
--   Expected: 1 row; page_number NULL

-- 3. No other submitted IDR is missing page numbers:
--   SELECT idr_id FROM icid.idrs
--   WHERE status = 'submitted' AND total_pages IS NULL
--     AND idr_id <> '8b2f887b-eff9-4ef6-a11b-03bb7d977cf3';
--   Expected: 0 rows

-- 4. Nothing outside the legacy pair depends on the legacy tables
--    (so CASCADE drops nothing unexpected):
--   SELECT conrelid::regclass AS from_table, conname
--   FROM pg_constraint
--   WHERE confrelid IN ('icid.reports'::regclass, 'icid.completed_forms'::regclass);
--   Expected: 1 row; icid.completed_forms, fk_completed_forms_report
--
--   SELECT DISTINCT v.oid::regclass AS dependent_view
--   FROM pg_depend d
--   JOIN pg_rewrite rw ON rw.oid = d.objid
--   JOIN pg_class v   ON v.oid  = rw.ev_class
--   WHERE d.refobjid IN ('icid.reports'::regclass, 'icid.completed_forms'::regclass)
--     AND v.oid NOT IN ('icid.reports'::regclass, 'icid.completed_forms'::regclass);
--   Expected: 0 rows

-- 5. Baseline counts:
--   SELECT
--     (SELECT COUNT(*) FROM icid.reports)         AS reports,          -- Expected: 3
--     (SELECT COUNT(*) FROM icid.completed_forms) AS completed_forms,  -- Expected: 1
--     (SELECT COUNT(*) FROM icid.idrs)            AS idrs,
--     (SELECT COUNT(*) FROM icid.idr_reports)     AS idr_reports;

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

-- 1. Backfill the Slice 5 IDR's page numbering
UPDATE icid.idrs
SET total_pages = 1
WHERE idr_id = '8b2f887b-eff9-4ef6-a11b-03bb7d977cf3'
  AND status = 'submitted'
  AND total_pages IS NULL;

UPDATE icid.idr_reports
SET page_number = 1
WHERE idr_id = '8b2f887b-eff9-4ef6-a11b-03bb7d977cf3'
  AND page_number IS NULL;

-- 2. Drop legacy tables (child first, so CASCADE on reports has nothing left to remove)
DROP TABLE icid.completed_forms CASCADE;
DROP TABLE icid.reports CASCADE;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Backfill applied:
--   SELECT i.total_pages, r.page_number
--   FROM icid.idrs i JOIN icid.idr_reports r ON r.idr_id = i.idr_id
--   WHERE i.idr_id = '8b2f887b-eff9-4ef6-a11b-03bb7d977cf3';
--   Expected: 1 row; 1, 1

-- B. No submitted IDR or its reports are left unnumbered:
--   SELECT COUNT(*) FROM icid.idrs WHERE status = 'submitted' AND total_pages IS NULL;  -- Expected: 0
--   SELECT COUNT(*) FROM icid.idr_reports r JOIN icid.idrs i ON i.idr_id = r.idr_id
--   WHERE i.status = 'submitted' AND r.page_number IS NULL;                             -- Expected: 0

-- C. Legacy tables gone:
--   SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'icid' AND table_name IN ('reports', 'completed_forms');
--   Expected: 0 rows

-- D. Live tables untouched:
--   SELECT COUNT(*) FROM icid.idrs;            -- Expected: same as pre-check 5
--   SELECT COUNT(*) FROM icid.idr_reports;     -- Expected: same as pre-check 5
--   SELECT COUNT(*) FROM icid.form_templates;  -- Expected: unchanged
