-- migrations/052_idr_unlock_columns.sql
--
-- Slice T2: an IDR records its last admin unlock (approved, or stuck in Stage 2, back to stage2_review): when, and by
-- which admin, beside the admin_unlock row icid.idr_audit already gets. One more foreign key to users
-- (last_unlocked_by; count_user_references counts it). Existing rows stay NULL: earlier unlocks are in idr_audit.
--
-- Run before deploying the T2 code. Safe to run twice.
--
-- Rollback (after rolling the code back): ALTER TABLE icid.idrs DROP COLUMN last_unlocked_at,
-- DROP COLUMN last_unlocked_by;

ALTER TABLE icid.idrs
    ADD COLUMN IF NOT EXISTS last_unlocked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_unlocked_by UUID REFERENCES icid.users(uuid);

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema = 'icid' AND table_name = 'idrs' AND column_name LIKE 'last_unlocked_%';  -- 2 rows
