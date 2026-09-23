-- migrations/003_add_reports_submitted_at.sql
-- Slice 3 (Submit a report): records when a report was submitted.
--   1. icid.reports  gains submitted_at TIMESTAMPTZ NULL
--      NULL for drafts; set once by POST /v1/reports/{report_id}/submit.
--
-- Run in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS).
-- Apply BEFORE running or deploying the Slice 3 backend: every reports query
-- selects submitted_at and will fail until this column exists.

------------------------------------------------------------
-- PRE-CHECK (run these on their own first, before the migration)
------------------------------------------------------------
-- Has this migration already run? Expected: 0 rows.
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'reports'
--     AND column_name = 'submitted_at';
--
-- Any reports already submitted? Their submitted_at will be NULL after this
-- migration (no reliable submit time exists for them). Expected: 0.
--   SELECT count(*) FROM icid.reports WHERE status = 'submitted';

BEGIN;

ALTER TABLE icid.reports
    ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;

COMMIT;

------------------------------------------------------------
-- VERIFY
------------------------------------------------------------
-- Column exists, nullable, timestamptz:
--   SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'reports'
--     AND column_name = 'submitted_at';
--   -> submitted_at | timestamp with time zone | YES
--
-- Existing drafts untouched:
--   SELECT count(*) FROM icid.reports WHERE submitted_at IS NOT NULL;
--   -> 0
