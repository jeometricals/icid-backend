-- migrations/014_demo_cleanup.sql
-- Demo mode (Slice H3, 2026-10-05): the backstop that removes abandoned demo users.
--
--   icid.cleanup_abandoned_demo_users() RETURNS INTEGER
--
-- A demo user (icid.users.is_demo) is deleted when they sign out. One who just closes the
-- tab is left behind; this function deletes every demo user created more than 24 hours ago
-- (their tokens have expired by then), with everything of theirs, children first:
--   report_attachments -> idr_reports -> idrs -> project_users -> users
-- the same order the sign-out delete uses (api/queries/users.py, delete_demo_user_rows).
-- It returns how many users it deleted, and never touches a user who isn't a demo user.
--
-- Storage is out of its reach: the attachment files of users it deletes stay in the
-- report-attachments bucket with no row pointing at them (sign-out removes files; this
-- doesn't). Known gap; a bucket sweep would have to find them.
--
-- SECURITY DEFINER with a pinned search_path, and EXECUTE revoked from PUBLIC, so only the
-- owner (and whoever it is granted to) can run it.
--
-- No table changes. Idempotent: CREATE OR REPLACE. Run in the Supabase SQL editor.
--
-- SCHEDULING: this migration only creates the function. Pick one way to run it daily:
--
--   1. pg_cron (in the database; enable the extension under Database -> Extensions first):
--        SELECT cron.schedule('cleanup-abandoned-demos', '0 3 * * *',
--                             $$SELECT icid.cleanup_abandoned_demo_users();$$);
--      To check or remove it:  SELECT * FROM cron.job;   SELECT cron.unschedule('cleanup-abandoned-demos');
--
--   2. Vercel Cron: an admin-only endpoint (POST /v1/admin/cleanup-demos, behind current_admin)
--      that calls the function, hit by a cron entry in vercel.json. Not built yet.
--
--   3. By hand, whenever:  SELECT icid.cleanup_abandoned_demo_users();

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Migration 013 has run (the function reads users.is_demo):
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'users' AND column_name = 'is_demo';
--   Expected: 1 row

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE OR REPLACE FUNCTION icid.cleanup_abandoned_demo_users()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = icid, pg_temp
AS $$
DECLARE
    stale UUID[];
    purged_count INTEGER;
BEGIN
    SELECT array_agg(u.uuid) INTO stale
    FROM icid.users u
    WHERE u.is_demo = true AND u.created_at < now() - interval '24 hours';

    IF stale IS NULL THEN
        RETURN 0;
    END IF;

    DELETE FROM icid.report_attachments a
    WHERE a.uploaded_by = ANY(stale)
       OR a.report_id IN (
           SELECT r.report_id
           FROM icid.idr_reports r
           JOIN icid.idrs i ON i.idr_id = r.idr_id
           WHERE i.reporter_uuid = ANY(stale)
       );

    DELETE FROM icid.idr_reports r
    WHERE r.idr_id IN (SELECT i.idr_id FROM icid.idrs i WHERE i.reporter_uuid = ANY(stale));

    DELETE FROM icid.idrs i WHERE i.reporter_uuid = ANY(stale);

    DELETE FROM icid.project_users pu WHERE pu.user_uuid = ANY(stale);

    DELETE FROM icid.users u WHERE u.uuid = ANY(stale) AND u.is_demo = true;
    GET DIAGNOSTICS purged_count = ROW_COUNT;

    RETURN purged_count;
END;
$$;

REVOKE ALL ON FUNCTION icid.cleanup_abandoned_demo_users() FROM PUBLIC;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The function exists:
--   SELECT proname, prosecdef FROM pg_proc
--   WHERE pronamespace = 'icid'::regnamespace AND proname = 'cleanup_abandoned_demo_users';
--   Expected: 1 row, prosecdef = true

-- B. It runs, and deletes nothing when no demo user is older than a day:
--   SELECT icid.cleanup_abandoned_demo_users();
--   Expected: 0 (or the number of stale demo users, if any exist)
