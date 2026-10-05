-- migrations/015_signatures.sql
-- Signatures (Slice I0, 2026-10-05): where a user's signature and an IDR's signed copy live.
--
--   icid.users                                  -- the user's current signature
--     + signature_path    TEXT                  -- object path in the signatures bucket; NULL = none yet
--     + signature_type    TEXT                  -- 'drawn' or 'uploaded' (chk_users_signature_type)
--     + signature_set_at  TIMESTAMPTZ           -- when the current signature was set
--
--   icid.idrs                                   -- the signature stamped at submit, never changed after
--     + inspector_signature_path  TEXT          -- object path of the signature the IDR was submitted with
--     + inspector_signed_at       TIMESTAMPTZ   -- when it was stamped
--
-- All five are nullable: no user has a signature yet, and IDRs submitted before this have no
-- signed copy. Nothing reads or writes them yet; the endpoints come in the next slices.
--
-- The files go in the private Storage bucket "signatures": run 015b_signatures_bucket.sql
-- after this.
--
-- Idempotent: every column is added IF NOT EXISTS (the CHECK comes with its column). Run in
-- the Supabase SQL editor. Everything between BEGIN and COMMIT is one transaction: if any
-- statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The new columns do not exist yet:
--   SELECT table_name, column_name FROM information_schema.columns
--   WHERE table_schema = 'icid'
--     AND column_name IN ('signature_path', 'signature_type', 'signature_set_at',
--                         'inspector_signature_path', 'inspector_signed_at');
--   Expected: 0 rows

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.users
    ADD COLUMN IF NOT EXISTS signature_path   TEXT,
    ADD COLUMN IF NOT EXISTS signature_type   TEXT
        CONSTRAINT chk_users_signature_type CHECK (signature_type IN ('drawn', 'uploaded')),
    ADD COLUMN IF NOT EXISTS signature_set_at TIMESTAMPTZ;

ALTER TABLE icid.idrs
    ADD COLUMN IF NOT EXISTS inspector_signature_path TEXT,
    ADD COLUMN IF NOT EXISTS inspector_signed_at      TIMESTAMPTZ;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Columns, types and nullability:
--   SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns
--   WHERE table_schema = 'icid'
--     AND column_name IN ('signature_path', 'signature_type', 'signature_set_at',
--                         'inspector_signature_path', 'inspector_signed_at')
--   ORDER BY table_name, ordinal_position;
--   Expected: 5 rows, all is_nullable YES (text, text, timestamp with time zone on users;
--             text, timestamp with time zone on idrs)

-- B. The CHECK on signature_type:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.users'::regclass AND conname = 'chk_users_signature_type';
--   Expected: 1 row, CHECK ((signature_type = ANY (ARRAY['drawn'::text, 'uploaded'::text])))
