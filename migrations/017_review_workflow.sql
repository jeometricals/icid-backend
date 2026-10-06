-- migrations/017_review_workflow.sql
-- Review workflow (Slice J0, 2026-10-06): the statuses and columns an IDR needs to go through review.
--
--   icid.idrs
--     ~ chk_idrs_status            now allows 'draft', 'submitted', 'stage1_review', 'stage2_review',
--                                  'approved', 'returned', 'deleted' (was 'draft', 'submitted')
--     + idr_number            TEXT          -- free text, set when a reviewer picks the IDR up at Stage 1
--     + stage1_reviewer_uuid  UUID          -- FK -> users.uuid; who picked it up at Stage 1
--     + stage1_reviewed_at    TIMESTAMPTZ
--     + re_reviewer_uuid      UUID          -- FK -> users.uuid; the RE who picked it up at Stage 2
--     + re_signature_path     TEXT          -- the RE's signature stamped at approval, like inspector_signature_path
--     + re_signed_at          TIMESTAMPTZ
--     + return_reason         TEXT          -- the latest return comment
--     + returned_from         TEXT          -- 'stage1' or 'stage2' (chk_idrs_returned_from)
--     + deleted_at            TIMESTAMPTZ   -- soft delete; the row is kept
--     + deleted_by            UUID          -- FK -> users.uuid
--     ~ idx_idrs_project_status    rebuilt as a partial index (WHERE deleted_at IS NULL)
--     + idx_idrs_status            (status) WHERE deleted_at IS NULL
--     + uq_idrs_project_number     UNIQUE (project_id, idr_number)
--                                  WHERE idr_number IS NOT NULL AND deleted_at IS NULL
--     ~ uq_idrs_project_reporter_date  from a UNIQUE constraint to a partial unique index of the
--                                  same name: (project_id, reporter_uuid, report_date)
--                                  WHERE deleted_at IS NULL
--
-- status itself is not added: the column, its default 'draft' and its NOT NULL date from
-- migration 004. Only the list of allowed values grows, so no existing row changes.
--
-- uq_idrs_project_number: an IDR number is used once per project among the IDRs that aren't
-- deleted. Soft-deleting an IDR frees its number.
--
-- uq_idrs_project_reporter_date: still one IDR per inspector per project per day, but a
-- soft-deleted IDR no longer holds the day. A partial unique index can't be a table
-- constraint, so the constraint is dropped and an index takes its name. An INSERT can no
-- longer name it as ON CONFLICT (project_id, reporter_uuid, report_date) without the index's
-- WHERE; api/queries/idrs.py uses a bare ON CONFLICT DO NOTHING, which works before and
-- after this migration.
--
-- Every new column is nullable and nothing reads or writes them yet; the endpoints come in
-- the next slices.
--
-- Idempotent: columns and new indexes are added IF NOT EXISTS; the status CHECK and
-- idx_idrs_project_status are dropped and re-created; the per-day constraint is dropped
-- IF EXISTS. Run in the Supabase SQL editor.
-- Everything between BEGIN and COMMIT is one transaction: if any statement fails, nothing
-- is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Every IDR is a draft or submitted (the values the old CHECK allowed):
--   SELECT status, count(*) FROM icid.idrs GROUP BY status ORDER BY status;
--   Expected: only 'draft' and 'submitted'

-- 2. The new columns do not exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idrs'
--     AND column_name IN ('idr_number', 'stage1_reviewer_uuid', 'stage1_reviewed_at', 're_reviewer_uuid',
--                         're_signature_path', 're_signed_at', 'return_reason', 'returned_from',
--                         'deleted_at', 'deleted_by');
--   Expected: 0 rows

-- 3. The status CHECK and the index this migration replaces are the ones from migration 004:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idrs'::regclass AND contype = 'c';
--   Expected: chk_idrs_status, CHECK ((status = ANY (ARRAY['draft'::text, 'submitted'::text])))
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE schemaname = 'icid' AND tablename = 'idrs' AND indexname = 'idx_idrs_project_status';
--   Expected: 1 row, ... USING btree (project_id, status), no WHERE

-- 4. The per-day rule is still the constraint from migration 004:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idrs'::regclass AND conname = 'uq_idrs_project_reporter_date';
--   Expected: 1 row, UNIQUE (project_id, reporter_uuid, report_date)

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.idrs
    ADD COLUMN IF NOT EXISTS idr_number           TEXT,
    ADD COLUMN IF NOT EXISTS stage1_reviewer_uuid UUID REFERENCES icid.users(uuid),
    ADD COLUMN IF NOT EXISTS stage1_reviewed_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS re_reviewer_uuid     UUID REFERENCES icid.users(uuid),
    ADD COLUMN IF NOT EXISTS re_signature_path    TEXT,
    ADD COLUMN IF NOT EXISTS re_signed_at         TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS return_reason        TEXT,
    ADD COLUMN IF NOT EXISTS returned_from        TEXT
        CONSTRAINT chk_idrs_returned_from CHECK (returned_from IN ('stage1', 'stage2')),
    ADD COLUMN IF NOT EXISTS deleted_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_by           UUID REFERENCES icid.users(uuid);

ALTER TABLE icid.idrs DROP CONSTRAINT IF EXISTS chk_idrs_status;

ALTER TABLE icid.idrs ADD CONSTRAINT chk_idrs_status CHECK (status IN (
    'draft', 'submitted', 'stage1_review', 'stage2_review', 'approved', 'returned', 'deleted'));

DROP INDEX IF EXISTS icid.idx_idrs_project_status;

CREATE INDEX idx_idrs_project_status ON icid.idrs(project_id, status) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_idrs_status ON icid.idrs(status) WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_idrs_project_number
    ON icid.idrs(project_id, idr_number)
    WHERE idr_number IS NOT NULL AND deleted_at IS NULL;

ALTER TABLE icid.idrs DROP CONSTRAINT IF EXISTS uq_idrs_project_reporter_date;

CREATE UNIQUE INDEX IF NOT EXISTS uq_idrs_project_reporter_date
    ON icid.idrs(project_id, reporter_uuid, report_date)
    WHERE deleted_at IS NULL;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The new columns, all nullable:
--   SELECT column_name, data_type, is_nullable FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idrs'
--     AND column_name IN ('idr_number', 'stage1_reviewer_uuid', 'stage1_reviewed_at', 're_reviewer_uuid',
--                         're_signature_path', 're_signed_at', 'return_reason', 'returned_from',
--                         'deleted_at', 'deleted_by')
--   ORDER BY ordinal_position;
--   Expected: 10 rows, all is_nullable YES

-- B. The CHECKs:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idrs'::regclass AND contype = 'c' ORDER BY conname;
--   Expected: chk_idrs_returned_from ('stage1', 'stage2') and chk_idrs_status with the seven statuses

-- C. The indexes:
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE schemaname = 'icid' AND tablename = 'idrs'
--     AND indexname IN ('idx_idrs_project_status', 'idx_idrs_status', 'uq_idrs_project_number',
--                       'uq_idrs_project_reporter_date');
--   Expected: 4 rows; the idx_ two end WHERE (deleted_at IS NULL); uq_idrs_project_number is UNIQUE on
--             (project_id, idr_number) WHERE ((idr_number IS NOT NULL) AND (deleted_at IS NULL));
--             uq_idrs_project_reporter_date is UNIQUE on (project_id, reporter_uuid, report_date)
--             WHERE (deleted_at IS NULL)

-- C2. The per-day rule is no longer a constraint:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'icid.idrs'::regclass AND conname = 'uq_idrs_project_reporter_date';
--   Expected: 0 rows

-- D. No IDR changed status:
--   SELECT status, count(*) FROM icid.idrs GROUP BY status ORDER BY status;
--   Expected: the same counts as pre-check 1
