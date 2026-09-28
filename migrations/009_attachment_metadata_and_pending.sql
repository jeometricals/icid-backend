-- migrations/009_attachment_metadata_and_pending.sql
-- Attachments: user-facing metadata and pending-upload state on report attachments.
--
--   icid.report_attachments gains:
--     attachment_name         TEXT NOT NULL                  -- non-blank, at most 200 characters
--     attachment_description  TEXT NOT NULL                  -- non-blank, at most 2000 characters
--     is_uploaded             BOOLEAN NOT NULL DEFAULT false -- true once the file is in Storage
--
--   CHECK constraints:
--     chk_report_attachments_name         btrim(attachment_name) <> '' AND char_length <= 200
--     chk_report_attachments_description  btrim(attachment_description) <> '' AND char_length <= 2000
--     chk_report_attachments_size         file_size_bytes > 0 AND <= 10485760 (re-asserted from 008)
--
-- attachment_name and attachment_description are NOT NULL with no DEFAULT because
-- the CHECK constraints (btrim not empty) prevent empty defaults. Both are
-- populated by the upload-request endpoint at insert time.
--
-- Idempotent: columns are added with IF NOT EXISTS and each CHECK is dropped
-- IF EXISTS before being re-added. Run in the Supabase SQL editor. Everything
-- between BEGIN and COMMIT is one transaction: if any statement fails, nothing
-- is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The new columns do not exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'report_attachments'
--     AND column_name IN ('attachment_name', 'attachment_description', 'is_uploaded');
--   Expected: 0 rows

-- 2. Row count (NOT NULL columns without a default cannot be added to existing rows):
--   SELECT COUNT(*) FROM icid.report_attachments;
--   Expected: 0

-- 3. Existing constraints:
--   SELECT conname, contype, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'icid.report_attachments'::regclass
--   ORDER BY contype, conname;
--   Expected: 5 rows, including chk_report_attachments_size

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.report_attachments
    ADD COLUMN IF NOT EXISTS attachment_name        TEXT    NOT NULL,
    ADD COLUMN IF NOT EXISTS attachment_description TEXT    NOT NULL,
    ADD COLUMN IF NOT EXISTS is_uploaded            BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE icid.report_attachments
    DROP CONSTRAINT IF EXISTS chk_report_attachments_name,
    DROP CONSTRAINT IF EXISTS chk_report_attachments_description,
    DROP CONSTRAINT IF EXISTS chk_report_attachments_size;

ALTER TABLE icid.report_attachments
    ADD CONSTRAINT chk_report_attachments_name
        CHECK (btrim(attachment_name) <> '' AND char_length(attachment_name) <= 200),
    ADD CONSTRAINT chk_report_attachments_description
        CHECK (btrim(attachment_description) <> '' AND char_length(attachment_description) <= 2000),
    ADD CONSTRAINT chk_report_attachments_size
        CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760);

COMMENT ON COLUMN icid.report_attachments.attachment_name IS
    'User-supplied display name for the attachment (1-200 characters, not blank).';
COMMENT ON COLUMN icid.report_attachments.attachment_description IS
    'User-supplied description of the attachment (1-2000 characters, not blank).';
COMMENT ON COLUMN icid.report_attachments.is_uploaded IS
    'False while the row is pending; true once the file has been stored in Supabase Storage at storage_path.';

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. New columns, types, nullability and defaults:
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'report_attachments'
--     AND column_name IN ('attachment_name', 'attachment_description', 'is_uploaded')
--   ORDER BY ordinal_position;
--   Expected: 3 rows, all is_nullable NO; attachment_name and attachment_description
--   text / no default (NULL), is_uploaded boolean / false

-- B. CHECK constraints:
--   SELECT conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'icid.report_attachments'::regclass AND contype = 'c'
--   ORDER BY conname;
--   Expected: 3 rows; chk_report_attachments_description, chk_report_attachments_name,
--   chk_report_attachments_size

-- C. Column comments:
--   SELECT a.attname, col_description(a.attrelid, a.attnum) AS comment
--   FROM pg_attribute a
--   WHERE a.attrelid = 'icid.report_attachments'::regclass
--     AND a.attname IN ('attachment_name', 'attachment_description', 'is_uploaded');
--   Expected: 3 rows, each with a non-null comment

-- D. Nothing else changed:
--   SELECT COUNT(*) FROM icid.report_attachments;  -- Expected: same as pre-check 2
