-- migrations/019_idr_audit.sql
-- IDR audit log (Slice J1, 2026-10-06): one row per thing done to an IDR in review.
--
--   icid.idr_audit
--     audit_id     UUID PK, gen_random_uuid()
--     idr_id       UUID NOT NULL          -- FK -> idrs.idr_id, ON DELETE CASCADE
--     actor_uuid   UUID NOT NULL          -- FK -> users.uuid; who did it
--     action       TEXT NOT NULL          -- 'submit', 'accept_stage1', 'approve_stage1', 'accept_stage2',
--                                         -- 'approve_stage2', 'return_to_inspector', 'return_to_oe'
--     from_status  TEXT                   -- NULL for actions that aren't a status change
--     to_status    TEXT
--     note         TEXT                   -- e.g. the return comment
--     created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
--     + idx_idr_audit_idr_created (idr_id, created_at DESC)
--
-- action has no CHECK, so a later slice can log new actions without a migration.
--
-- Each row is written by the same statement that changes the IDR (a data-modifying CTE), so
-- an IDR never changes status without its row. Nothing reads the table yet.
--
-- ON DELETE CASCADE: the only IDRs ever hard-deleted are demo users' (at sign-out and by the
-- daily cleanup), and their rows go with them. Review's own delete is a soft delete.
--
-- The API writes to this table from the moment J1 is deployed (submit included), so run this
-- before deploying J1.
--
-- Idempotent: the table and the index are created IF NOT EXISTS. Run in the Supabase SQL
-- editor, after 017. Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The table does not exist yet:
--   SELECT to_regclass('icid.idr_audit');
--   Expected: NULL

-- 2. Migration 017 has been applied (the review columns the API writes alongside each row):
--   SELECT count(*) FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idrs' AND column_name IN ('idr_number', 'deleted_at');
--   Expected: 2

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS icid.idr_audit (
    audit_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idr_id       UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    actor_uuid   UUID NOT NULL REFERENCES icid.users(uuid),
    action       TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_idr_audit_idr_created ON icid.idr_audit(idr_id, created_at DESC);

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Columns:
--   SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idr_audit' ORDER BY ordinal_position;
--   Expected: 8 rows; audit_id, idr_id, actor_uuid, action and created_at are NOT NULL

-- B. Foreign keys:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idr_audit'::regclass AND contype = 'f';
--   Expected: idr_id -> icid.idrs(idr_id) ON DELETE CASCADE, actor_uuid -> icid.users(uuid)

-- C. Index:
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'idx_idr_audit_idr_created';
--   Expected: ... USING btree (idr_id, created_at DESC)
