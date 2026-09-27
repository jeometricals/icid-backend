-- migrations/008_report_attachments.sql
-- Attachments Slice A1: metadata table for files attached to a report.
--
--   icid.report_attachments  -- one row per file; the file itself lives in the
--                               private Supabase Storage bucket report-attachments
--                               at storage_path ({report_id}/{attachment_id}_{file_name}).
--
-- Deleting a report deletes its attachment rows (ON DELETE CASCADE); the backend
-- removes the Storage files separately, because Storage is not part of this
-- transaction. Run in the Supabase SQL editor. Everything between BEGIN and
-- COMMIT is one transaction: if any statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Table should not exist yet:
--   SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'icid' AND table_name = 'report_attachments';
--   Expected: 0 rows

-- 2. uuid-ossp is installed (every icid table defaults its id with uuid_generate_v4()):
--   SELECT extname FROM pg_extension WHERE extname = 'uuid-ossp';
--   Expected: 1 row

-- 3. The referenced keys exist:
--   SELECT table_name, column_name FROM information_schema.columns
--   WHERE table_schema = 'icid'
--     AND (table_name, column_name) IN (('idr_reports', 'report_id'), ('users', 'uuid'));
--   Expected: 2 rows

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE icid.report_attachments (
    attachment_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id        UUID NOT NULL REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE,
    file_name        TEXT NOT NULL,
    file_type        TEXT NOT NULL,
    file_size_bytes  INTEGER NOT NULL,
    storage_path     TEXT NOT NULL UNIQUE,
    uploaded_by      UUID NOT NULL REFERENCES icid.users(uuid),
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_report_attachments_size
        CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760)
);

CREATE INDEX idx_report_attachments_report_id ON icid.report_attachments(report_id);

COMMENT ON TABLE icid.report_attachments IS
    'Files attached to an idr_report. Bytes live in Supabase Storage (bucket report-attachments) at storage_path; this row is the metadata.';

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Columns, types and nullability:
--   SELECT column_name, data_type, is_nullable, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'report_attachments'
--   ORDER BY ordinal_position;
--   Expected: 8 rows, all is_nullable NO; attachment_id defaults to uuid_generate_v4(),
--   uploaded_at to now()

-- B. Constraints (PK, 2 FKs, UNIQUE storage_path, size CHECK), with the report FK cascading:
--   SELECT conname, contype, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid = 'icid.report_attachments'::regclass
--   ORDER BY contype, conname;
--   Expected: 5 rows; the FK to idr_reports ends in ON DELETE CASCADE

-- C. Index exists:
--   SELECT indexname FROM pg_indexes
--   WHERE schemaname = 'icid' AND tablename = 'report_attachments';
--   Expected: report_attachments_pkey, report_attachments_storage_path_key,
--   idx_report_attachments_report_id

-- D. Table starts empty and nothing else changed:
--   SELECT COUNT(*) FROM icid.report_attachments;  -- Expected: 0
--   SELECT COUNT(*) FROM icid.idr_reports;         -- Expected: same as before
