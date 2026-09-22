-- migrations/002_report_id_uuid.sql
-- Slice 1 (Save a draft): makes reports/completed_forms ready for save operations.
--   1. icid.reports.report_id  TEXT -> UUID DEFAULT uuid_generate_v4()
--      Existing ids (R1, R2, R3, ...) are remapped to fresh UUIDs, and
--      icid.completed_forms.report_id is remapped and converted to match.
--   2. icid.completed_forms.completed_form_id  gains DEFAULT uuid_generate_v4()::text
--   3. icid.completed_forms  UNIQUE (report_id, form_template_id)
--
-- Run in the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

------------------------------------------------------------
-- PRE-CHECK (run these on their own first, before the migration)
------------------------------------------------------------
-- Has this migration already run? Must return data_type = 'text' for both rows.
--   SELECT table_name, data_type FROM information_schema.columns
--   WHERE table_schema = 'icid' AND column_name = 'report_id'
--     AND table_name IN ('reports', 'completed_forms');
--
-- How many rows will be remapped?
--   SELECT
--     (SELECT count(*) FROM icid.reports)         AS reports,
--     (SELECT count(*) FROM icid.completed_forms) AS completed_forms;
--
-- Does anything other than completed_forms reference icid.reports? Must return
-- exactly one row: fk_completed_forms_report. Any other row means a table this
-- migration does not remap -- stop and extend the migration first.
--   SELECT conrelid::regclass AS referencing_table, conname
--   FROM pg_constraint
--   WHERE contype = 'f' AND confrelid = 'icid.reports'::regclass;
--
-- Any views or RLS policies on these tables? Must return 0 rows, or the
-- column type change below will fail (and roll back the whole migration).
--   SELECT view_schema, view_name, table_name, column_name
--   FROM information_schema.view_column_usage
--   WHERE table_schema = 'icid'
--     AND table_name IN ('reports', 'completed_forms')
--     AND column_name = 'report_id';
--   SELECT schemaname, tablename, policyname
--   FROM pg_policies
--   WHERE schemaname = 'icid' AND tablename IN ('reports', 'completed_forms');
--
-- Any report with more than one form of the same template? Must return 0 rows,
-- or the UNIQUE constraint below will fail (and roll back the whole migration).
--   SELECT report_id, form_template_id, count(*)
--   FROM icid.completed_forms
--   GROUP BY report_id, form_template_id
--   HAVING count(*) > 1;
--
-- Is uuid_generate_v4() callable from the SQL editor? Must return a UUID.
--   SELECT uuid_generate_v4();

BEGIN;

------------------------------------------------------------
-- 1. REPORTS.report_id TEXT -> UUID (remapping every reference)
------------------------------------------------------------
-- One new UUID per existing report id. Dropped automatically at COMMIT.
CREATE TEMP TABLE report_id_map ON COMMIT DROP AS
SELECT report_id AS old_id, uuid_generate_v4() AS new_id
FROM icid.reports;

-- The FK would block rewriting ids on either side; re-added below.
ALTER TABLE icid.completed_forms
    DROP CONSTRAINT fk_completed_forms_report;

-- updated_at is deliberately left alone: remapping a key is not a content edit.
UPDATE icid.completed_forms cf
SET report_id = m.new_id::text
FROM report_id_map m
WHERE cf.report_id = m.old_id;

UPDATE icid.reports r
SET report_id = m.new_id::text
FROM report_id_map m
WHERE r.report_id = m.old_id;

ALTER TABLE icid.reports
    ALTER COLUMN report_id TYPE UUID USING report_id::uuid;

ALTER TABLE icid.reports
    ALTER COLUMN report_id SET DEFAULT uuid_generate_v4();

ALTER TABLE icid.completed_forms
    ALTER COLUMN report_id TYPE UUID USING report_id::uuid;

ALTER TABLE icid.completed_forms
    ADD CONSTRAINT fk_completed_forms_report
        FOREIGN KEY (report_id) REFERENCES icid.reports(report_id);

------------------------------------------------------------
-- 2. COMPLETED_FORMS.completed_form_id default
------------------------------------------------------------
ALTER TABLE icid.completed_forms
    ALTER COLUMN completed_form_id SET DEFAULT uuid_generate_v4()::text;

------------------------------------------------------------
-- 3. COMPLETED_FORMS: one form per template per report
------------------------------------------------------------
ALTER TABLE icid.completed_forms
    ADD CONSTRAINT uq_completed_forms_report_template
        UNIQUE (report_id, form_template_id);

COMMIT;

------------------------------------------------------------
-- VERIFY (run after COMMIT)
------------------------------------------------------------
-- Both report_id columns are uuid; reports.report_id and completed_form_id have defaults.
--   SELECT table_name, column_name, data_type, column_default
--   FROM information_schema.columns
--   WHERE table_schema = 'icid'
--     AND table_name IN ('reports', 'completed_forms')
--     AND column_name IN ('report_id', 'completed_form_id');
--
-- Row counts match the pre-check.
--   SELECT
--     (SELECT count(*) FROM icid.reports)         AS reports,
--     (SELECT count(*) FROM icid.completed_forms) AS completed_forms;
--
-- FK and UNIQUE constraints are in place. Must return 2 rows.
--   SELECT conname, contype FROM pg_constraint
--   WHERE conrelid = 'icid.completed_forms'::regclass
--     AND conname IN ('fk_completed_forms_report', 'uq_completed_forms_report_template');
--
-- A new report gets a generated id (rolled back, so nothing is left behind).
--   BEGIN;
--   INSERT INTO icid.reports (reporter_uuid, project_id)
--   SELECT uuid, 'HWS0023' FROM icid.users LIMIT 1
--   RETURNING report_id, status;
--   ROLLBACK;
