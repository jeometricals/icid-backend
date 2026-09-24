-- migrations/004_idr_refactor.sql
-- Phase R, Slice R1: Refactor to IDR (Inspector Daily Diary) model.
--
-- Introduces:
--   1. icid.idrs           - new parent table (one per inspector per project per day)
--   2. icid.idr_reports    - new child table (one per report within an IDR)
--
-- Migrates existing icid.reports + icid.completed_forms data into new tables.
-- OLD TABLES ARE NOT DROPPED IN THIS MIGRATION - deferred to Slice R5.
--
-- Run in Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. New tables should not exist yet:
--   SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'icid' AND table_name IN ('idrs', 'idr_reports');
--   Expected: 0 rows

-- 2. Current row counts (baseline):
--   SELECT
--     (SELECT COUNT(*) FROM icid.reports) AS reports,
--     (SELECT COUNT(*) FROM icid.completed_forms) AS completed_forms;
--   Expected: 3 reports, 1 completed_form

-- 3. No date collisions per (project_id, reporter_uuid, report_date):
--   SELECT project_id, reporter_uuid, report_date, COUNT(*)
--   FROM icid.reports
--   GROUP BY project_id, reporter_uuid, report_date
--   HAVING COUNT(*) > 1;
--   Expected: 0 rows

-- 4. Every completed_form's report_id must exist in reports:
--   SELECT cf.completed_form_id, cf.report_id
--   FROM icid.completed_forms cf
--   LEFT JOIN icid.reports r ON r.report_id = cf.report_id
--   WHERE r.report_id IS NULL;
--   Expected: 0 rows

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

-- 1. CREATE idrs TABLE
CREATE TABLE icid.idrs (
    idr_id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id             TEXT NOT NULL REFERENCES icid.projects(project_id),
    reporter_uuid          UUID NOT NULL REFERENCES icid.users(uuid),
    report_date            DATE NOT NULL,
    work_start_time        TIME NULL,
    work_end_time          TIME NULL,
    inspector_start_time   TIME NULL,
    inspector_end_time     TIME NULL,
    temp_low               NUMERIC(4,1) NULL,
    temp_high              NUMERIC(4,1) NULL,
    weather_am             TEXT NULL,
    weather_pm             TEXT NULL,
    total_pages            INTEGER NULL,
    status                 TEXT NOT NULL DEFAULT 'draft',
    submitted_at           TIMESTAMPTZ NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_idrs_status CHECK (status IN ('draft', 'submitted')),
    CONSTRAINT uq_idrs_project_reporter_date UNIQUE (project_id, reporter_uuid, report_date)
);

-- Indexes for common query patterns
CREATE INDEX idx_idrs_project_status ON icid.idrs(project_id, status);
CREATE INDEX idx_idrs_reporter ON icid.idrs(reporter_uuid);


-- 2. CREATE idr_reports TABLE
CREATE TABLE icid.idr_reports (
    report_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    idr_id                UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    report_type           TEXT NOT NULL,
    is_addendum           BOOLEAN NOT NULL DEFAULT false,
    parent_report_id      UUID NULL REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE,
    page_number           INTEGER NULL,
    report_data           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_idr_reports_parent CHECK (
        (is_addendum = false AND parent_report_id IS NULL)
        OR (is_addendum = true)
    )
);

-- Indexes
CREATE INDEX idx_idr_reports_idr ON icid.idr_reports(idr_id);
CREATE INDEX idx_idr_reports_parent ON icid.idr_reports(parent_report_id) WHERE parent_report_id IS NOT NULL;


-- 3. MIGRATE DATA - reports -> idrs + idr_reports
--
-- Each existing report becomes one IDR + one idr_report (General type).
-- The report's completed_form (if any) becomes the report_data.
-- Uses a CTE + RETURNING to capture the mapping between old report_id and new idr_id.

WITH old_reports AS (
    SELECT
        r.report_id AS old_report_id,
        r.reporter_uuid,
        r.project_id,
        r.report_date,
        r.status,
        r.submitted_at,
        r.created_at,
        r.updated_at,
        cf.form_data,
        cf.updated_at AS form_updated_at
    FROM icid.reports r
    LEFT JOIN icid.completed_forms cf ON cf.report_id = r.report_id
),
inserted_idrs AS (
    INSERT INTO icid.idrs (
        project_id, reporter_uuid, report_date, status,
        submitted_at, created_at, updated_at
    )
    SELECT
        project_id, reporter_uuid, report_date, status,
        submitted_at, created_at, updated_at
    FROM old_reports
    RETURNING idr_id, project_id, reporter_uuid, report_date
)
INSERT INTO icid.idr_reports (
    idr_id, report_type, is_addendum, report_data, created_at, updated_at
)
SELECT
    i.idr_id,
    'GEN' AS report_type,
    false AS is_addendum,
    COALESCE(o.form_data, '{}'::jsonb) AS report_data,
    o.created_at,
    COALESCE(o.form_updated_at, o.updated_at) AS updated_at
FROM old_reports o
JOIN inserted_idrs i
    ON i.project_id = o.project_id
    AND i.reporter_uuid = o.reporter_uuid
    AND i.report_date = o.report_date;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Row count matches - one IDR per old report:
--   SELECT
--     (SELECT COUNT(*) FROM icid.reports)     AS old_reports,
--     (SELECT COUNT(*) FROM icid.idrs)        AS new_idrs,
--     (SELECT COUNT(*) FROM icid.idr_reports) AS new_idr_reports;
--   Expected: old_reports = new_idrs = new_idr_reports = 3

-- B. Each IDR has exactly one General child:
--   SELECT idr_id, COUNT(*) AS report_count
--   FROM icid.idr_reports
--   GROUP BY idr_id
--   HAVING COUNT(*) != 1;
--   Expected: 0 rows

-- C. Submitted status carried over:
--   SELECT status, COUNT(*) FROM icid.idrs GROUP BY status;
--   Expected: draft=2, submitted=1

-- D. Report data preserved for the submitted row:
--   SELECT ir.report_data->>'description' AS description
--   FROM icid.idr_reports ir
--   JOIN icid.idrs i ON i.idr_id = ir.idr_id
--   WHERE i.status = 'submitted';
--   Expected: 'Test For Slice 5 - 9/24/26 12:04PM'

-- E. All report_types are 'GEN' (nothing else exists yet):
--   SELECT report_type, COUNT(*) FROM icid.idr_reports GROUP BY report_type;
--   Expected: GEN=3

-- F. Test auto-generation defaults work:
--   BEGIN;
--   INSERT INTO icid.idrs (project_id, reporter_uuid, report_date)
--   SELECT 'HWS0023', uuid, '2099-01-01' FROM icid.users LIMIT 1
--   RETURNING idr_id, status, created_at;
--   ROLLBACK;
--   Expected: 1 row, status='draft', created_at=now(), idr_id=fresh UUID