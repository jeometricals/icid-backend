-- migrations/001_slice1_schema.sql
-- Slice 1 (Save a draft): brings an existing database in line with schema.sql.
--   1. icid.reports.status  TEXT NOT NULL DEFAULT 'draft', CHECK draft|submitted
--   2. icid.completed_forms.form_data  TEXT -> JSONB
--   3. icid.form_templates  GENERAL row (skipped if it already exists)
--
-- Run in the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

------------------------------------------------------------
-- PRE-CHECK (run these on their own first, before the migration)
------------------------------------------------------------
-- How many completed forms exist?
--   SELECT count(*) FROM icid.completed_forms;
--
-- Any form_data values that are not valid JSON? Must return 0 rows, or the
-- JSONB conversion below will fail (and roll back the whole migration).
-- Postgres 16+ (check with: SELECT version();):
--   SELECT completed_form_id, left(form_data, 80) AS preview
--   FROM icid.completed_forms
--   WHERE form_data IS NOT NULL AND NOT (form_data IS JSON);
--
-- Postgres 15 or older (no IS JSON): list the values and inspect them by eye:
--   SELECT completed_form_id, left(form_data, 80) AS preview
--   FROM icid.completed_forms
--   WHERE form_data IS NOT NULL;
--
-- Does reports.status already exist? Must return 0 rows.
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'reports' AND column_name = 'status';

BEGIN;

------------------------------------------------------------
-- 1. REPORTS: status column
------------------------------------------------------------
ALTER TABLE icid.reports
    ADD COLUMN status TEXT NOT NULL DEFAULT 'draft';

ALTER TABLE icid.reports
    ADD CONSTRAINT chk_reports_status
        CHECK (status IN ('draft', 'submitted'));

------------------------------------------------------------
-- 2. COMPLETED_FORMS: form_data TEXT -> JSONB
------------------------------------------------------------
ALTER TABLE icid.completed_forms
    ALTER COLUMN form_data TYPE JSONB USING form_data::jsonb;

------------------------------------------------------------
-- 3. FORM_TEMPLATES: GENERAL template row (matches seed.sql)
------------------------------------------------------------
INSERT INTO icid.form_templates (form_template_id, form_name, form_description, form_status)
VALUES ('GENERAL', 'General Form', 'Daily general inspection report: description of work, pay items, workforce, equipment and safety checklist.', 'active')
ON CONFLICT (form_template_id) DO NOTHING;

COMMIT;
