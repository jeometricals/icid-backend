-- migrations/022_stage_accepted_at.sql
-- Stage acceptance times (Slice K2.6, 2026-10-06): when each review stage was last accepted, on
-- the IDR itself.
--
--   icid.idrs
--     + stage1_accepted_at   TIMESTAMPTZ   when the Stage 1 reviewer last accepted the IDR
--     + stage2_accepted_at   TIMESTAMPTZ   when the RE last accepted it
--
-- Both nullable, no default, no backfill: an IDR accepted before this migration keeps NULL, which
-- reads as "acceptance time unknown" (every attestation at the stage counts, whenever it was made).
--
-- The API keeps them:
--   accept-stage1            stage1_accepted_at = now(), stage2_accepted_at = NULL
--   accept-stage2            stage2_accepted_at = now()
--   return to the inspector  both NULL
--   return to the OE         stage2_accepted_at = NULL
--   admin unlock             stage2_accepted_at = NULL
--
-- They mark the start of a stage's round of review: a pay-item approval, revision or add made
-- before it no longer counts towards approving the stage. Until now that time was looked up in
-- icid.idr_audit (the latest accept_stage1 / accept_stage2 row); idr_audit itself is unchanged.
--
-- No existing row changes. Idempotent: ADD COLUMN IF NOT EXISTS. Run in the Supabase SQL editor,
-- after 021. Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Neither column exists yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idrs'
--     AND column_name IN ('stage1_accepted_at', 'stage2_accepted_at');
--   Expected: no rows

-- 2. The IDRs there are, by status (to compare afterwards):
--   SELECT status, count(*) FROM icid.idrs GROUP BY status ORDER BY status;

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.idrs
    ADD COLUMN IF NOT EXISTS stage1_accepted_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS stage2_accepted_at   TIMESTAMPTZ;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Both columns are there, nullable, with no default:
--   SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idrs'
--     AND column_name IN ('stage1_accepted_at', 'stage2_accepted_at') ORDER BY column_name;
--   Expected: two rows, 'timestamp with time zone', YES, NULL

-- B. No IDR has either set:
--   SELECT count(*) FROM icid.idrs WHERE stage1_accepted_at IS NOT NULL OR stage2_accepted_at IS NOT NULL;
--   Expected: 0

-- C. No IDR changed status:
--   SELECT status, count(*) FROM icid.idrs GROUP BY status ORDER BY status;
--   Expected: the same counts as pre-check 2
