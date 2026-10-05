-- migrations/016_users_email_lower.sql
-- Housekeeping (2026-10-05): one account per email, whatever its capitals.
--
--   icid.users
--     + idx_users_email_lower  UNIQUE INDEX (lower(email))
--     - uq_users_email         UNIQUE (email), from migration 013
--     - idx_users_email        plain index on email, from the baseline
--
-- Sign-in has always looked an email up without regard to case (WHERE lower(email) = ...),
-- but uq_users_email was case-sensitive: "Reza@icid.local" and "reza@icid.local" could
-- both exist, and sign-in would pick the older. The new index closes that, and serves the
-- sign-in lookup, which neither old index could. Emails are still stored as entered.
--
-- seed_auth_users.sql upserts ON CONFLICT (lower(email)), so it needs this migration first.
--
-- Idempotent. Run in the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails (e.g. the index can't be built), nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. No two users share an email once case is ignored (the unique index fails otherwise).
--    If any do, decide which row keeps the address and change the other before going on:
--   SELECT lower(email), count(*) FROM icid.users GROUP BY lower(email) HAVING count(*) > 1;
--   Expected: 0 rows

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON icid.users (lower(email));

ALTER TABLE icid.users DROP CONSTRAINT IF EXISTS uq_users_email;

DROP INDEX IF EXISTS icid.idx_users_email;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The indexes on users.email:
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE schemaname = 'icid' AND tablename = 'users' AND indexdef ILIKE '%email%';
--   Expected: 1 row, idx_users_email_lower: CREATE UNIQUE INDEX ... USING btree (lower(email))

-- B. The old constraint is gone:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'icid.users'::regclass AND conname = 'uq_users_email';
--   Expected: 0 rows
