-- migrations/051_ped_ramp_inspection_unlock.sql
--
-- Slice T1: an admin can unlock an approved ped ramp inspection (back to draft, its approved TIFs back to draft too).
-- The inspection records the last unlock: when, and by which admin. One more foreign key to users (last_unlocked_by;
-- count_user_references counts it). The DOT forms filed at the earlier approval stay in pedramp-exports, unlinked.
--
-- Run before deploying the T1 code. Safe to run twice.
--
-- Rollback (after rolling the code back): ALTER TABLE icid.ped_ramp_inspections DROP COLUMN last_unlocked_at,
-- DROP COLUMN last_unlocked_by;

ALTER TABLE icid.ped_ramp_inspections
    ADD COLUMN IF NOT EXISTS last_unlocked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_unlocked_by UUID REFERENCES icid.users(uuid);

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT column_name FROM information_schema.columns
--  WHERE table_schema = 'icid' AND table_name = 'ped_ramp_inspections' AND column_name LIKE 'last_unlocked_%';  -- 2 rows
