-- migrations/055_report_site_updated.sql
--
-- Slice V2: who last changed the work at an imported site, and when. Set by the site PATCH statement itself, only when
-- work_type or work_type_other changes (no trigger: a trigger can't know the signed-in user). Existing rows stay NULL;
-- the UI falls back to added_at / added_by. One more foreign key to users (updated_by; count_user_references counts it).
--
-- Two nullable columns: nothing in the live data is touched. Run with 054, before deploying the Slice V code. Safe to
-- run twice.
--
-- Rollback (after rolling the code back): ALTER TABLE icid.idr_report_sites DROP COLUMN updated_by, DROP COLUMN updated_at;

BEGIN;

ALTER TABLE icid.idr_report_sites
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES icid.users(uuid);

COMMIT;
