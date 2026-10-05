-- migrations/013_auth_users.sql
-- Auth groundwork (Slice H0, 2026-10-05): the columns sign-in needs on icid.users.
--
--   icid.users
--     + password_hash  TEXT                            -- bcrypt; NULL = can't sign in with a password
--     + role           TEXT                            -- e.g. 'admin'; NULL = no role yet
--     + is_demo        BOOLEAN NOT NULL DEFAULT false  -- throwaway demo accounts
--     + uq_users_email UNIQUE (email)
--     + idx_users_is_demo (partial, WHERE is_demo = true)
--
-- password_hash is nullable so the users already there (none has a password) pass.
-- The partial index keeps the daily demo cleanup fast:
--   DELETE FROM icid.users WHERE is_demo = true AND created_at < now() - interval '24 hours';
--
-- No name column: first_name / last_name stay the only name fields.
-- idx_users_email (the baseline's plain index) is left in place; the UNIQUE constraint
-- brings its own index, so the old one is now redundant and can be dropped later.
--
-- Idempotent: columns and the index are added IF NOT EXISTS, the constraint only when
-- missing. Run in the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. No two users share an email (the UNIQUE constraint fails otherwise):
--   SELECT email, count(*) FROM icid.users GROUP BY email HAVING count(*) > 1;
--   Expected: 0 rows

-- 2. The new columns do not exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'users'
--     AND column_name IN ('password_hash', 'role', 'is_demo');
--   Expected: 0 rows

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.users
    ADD COLUMN IF NOT EXISTS password_hash TEXT,
    ADD COLUMN IF NOT EXISTS role          TEXT,
    ADD COLUMN IF NOT EXISTS is_demo       BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_users_email' AND conrelid = 'icid.users'::regclass
    ) THEN
        ALTER TABLE icid.users ADD CONSTRAINT uq_users_email UNIQUE (email);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_is_demo ON icid.users(is_demo) WHERE is_demo = true;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Columns, types and nullability:
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'users'
--   ORDER BY ordinal_position;
--   Expected: password_hash text YES, role text YES, is_demo boolean NO false

-- B. Constraint:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'icid.users'::regclass AND conname = 'uq_users_email';
--   Expected: 1 row

-- C. Index:
--   SELECT indexdef FROM pg_indexes
--   WHERE schemaname = 'icid' AND indexname = 'idx_users_is_demo';
--   Expected: ... ON icid.users USING btree (is_demo) WHERE (is_demo = true)
