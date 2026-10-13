-- migrations/028_users_soft_delete.sql
-- Soft delete for users (Slice P3, 2026-10-13): an admin can delete a user without losing what the user
-- did, bring them back, and reuse the email of a deleted account.
--
--   icid.users   (changed)
--     + deleted_at  TIMESTAMPTZ NULL   when an admin deleted the user; NULL for a user who isn't deleted
--     + deleted_by  UUID NULL          which admin did; FK -> users.uuid ON DELETE SET NULL
--     idx_users_email_lower            was UNIQUE (lower(email)); now the same with WHERE deleted_at IS NULL
--
-- A deleted user keeps their row, their email and everything that names them (IDRs, reviews, roles,
-- audit rows); they just can't sign in. The unique index now covers only users who aren't deleted, so
-- a new account can take a deleted account's email, and two deleted accounts can share one.
--
-- No row changes: every existing user gets deleted_at NULL and stays under the index. There is no 027.
--
-- ORDER MATTERS, both ways:
--   * The P3 code reads deleted_at on every request that checks a token, so run this BEFORE deploying it.
--   * The code from before P3 creates users with ON CONFLICT (lower(email)), which needs a unique index
--     with no WHERE. Between this migration and the P3 deploy, POST /v1/admin/users fails (500); nothing
--     else is affected. So deploy P3 right after running this. seed_auth_users.sql (updated with P3)
--     likewise needs this migration first.
--
-- Run in the Supabase SQL editor, after 026. Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The columns don't exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'users' AND column_name IN ('deleted_at', 'deleted_by');
--   Expected: no rows

-- 2. The email index is the one from migration 016, with no WHERE:
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'idx_users_email_lower';
--   Expected: CREATE UNIQUE INDEX idx_users_email_lower ON icid.users USING btree (lower(email))

-- 3. How many users there are; note it for check C:
--   SELECT count(*) FROM icid.users;

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.users
    ADD COLUMN deleted_at TIMESTAMPTZ,
    ADD COLUMN deleted_by UUID REFERENCES icid.users(uuid) ON DELETE SET NULL;

DROP INDEX icid.idx_users_email_lower;

CREATE UNIQUE INDEX idx_users_email_lower ON icid.users (lower(email)) WHERE deleted_at IS NULL;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The two columns, both nullable:
--   SELECT column_name, data_type, is_nullable FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'users' AND column_name IN ('deleted_at', 'deleted_by')
--   ORDER BY column_name;
--   Expected: deleted_at | timestamp with time zone | YES and deleted_by | uuid | YES

-- B. The index is partial, and deleted_by points at users:
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'idx_users_email_lower';
--   Expected: CREATE UNIQUE INDEX idx_users_email_lower ON icid.users USING btree (lower(email))
--             WHERE (deleted_at IS NULL)
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.users'::regclass AND contype = 'f' AND conname = 'users_deleted_by_fkey';
--   Expected: FOREIGN KEY (deleted_by) REFERENCES icid.users(uuid) ON DELETE SET NULL

-- C. Nobody was deleted or lost:
--   SELECT count(*) AS users, count(deleted_at) AS deleted FROM icid.users;
--   Expected: the count from pre-check 3, and 0

-- ============================================================
-- ROLLBACK (only while no two users share an email without regard to case; check with
--   SELECT lower(email), count(*) FROM icid.users GROUP BY lower(email) HAVING count(*) > 1;
-- Deleted users become ordinary users again: decide what to do with them first.)
-- ============================================================

-- BEGIN;
-- DROP INDEX icid.idx_users_email_lower;
-- CREATE UNIQUE INDEX idx_users_email_lower ON icid.users (lower(email));
-- ALTER TABLE icid.users DROP COLUMN deleted_by, DROP COLUMN deleted_at;
-- COMMIT;
