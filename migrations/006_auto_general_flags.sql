-- migrations/006_auto_general_flags.sql
-- Phase R, Slice R4a: flags for backend-managed auto-summary General reports.
--
--   icid.idr_reports.is_auto_generated      -- true only for a backend-created General
--   icid.idrs.has_dismissed_auto_general    -- true once an inspector deletes the auto-General
--
-- Both are additive, NOT NULL with a false default — no backfill needed. Every
-- existing GEN report is inspector-created, so false is correct for them. Run in
-- the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Columns should not exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idr_reports'
--     AND column_name = 'is_auto_generated';
--   Expected: 0 rows
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idrs'
--     AND column_name = 'has_dismissed_auto_general';
--   Expected: 0 rows

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.idr_reports
    ADD COLUMN is_auto_generated boolean NOT NULL DEFAULT false;

ALTER TABLE icid.idrs
    ADD COLUMN has_dismissed_auto_general boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN icid.idr_reports.is_auto_generated IS
    'True only for a backend-created auto-summary General report. Inspector-created reports are always false.';

COMMENT ON COLUMN icid.idrs.has_dismissed_auto_general IS
    'True once an inspector deletes the auto-generated General. Blocks the backend from re-creating it.';

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Both columns exist, boolean, NOT NULL, default false:
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'icid'
--     AND (table_name, column_name) IN
--         (('idr_reports', 'is_auto_generated'), ('idrs', 'has_dismissed_auto_general'));
--   Expected: 2 rows; data_type boolean, is_nullable NO, column_default false

-- B. No existing row was flagged by the default:
--   SELECT COUNT(*) FROM icid.idr_reports WHERE is_auto_generated;      -- Expected: 0
--   SELECT COUNT(*) FROM icid.idrs WHERE has_dismissed_auto_general;    -- Expected: 0

-- C. Row counts unchanged (additive-only migration):
--   SELECT COUNT(*) FROM icid.idr_reports;   -- Expected: same as before
--   SELECT COUNT(*) FROM icid.idrs;          -- Expected: same as before
