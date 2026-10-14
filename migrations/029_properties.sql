-- migrations/029_properties.sql
-- Properties (Slice R0a, 2026-10-14): the tax lots a project's work is done on, one row per property,
-- each named by its BBL (borough, block and lot).
--
--   icid.properties   (new)
--     id             UUID PK
--     project_id     TEXT NOT NULL      FK -> projects.project_id
--     bbl            TEXT NOT NULL      unique per project among properties that aren't deleted
--     address        TEXT NOT NULL
--     property_type  TEXT NOT NULL      CHECK: 1_3_family | 4_plus_family | expedited
--     notes          TEXT NULL
--     created_at, updated_at
--     created_by     UUID NOT NULL      FK -> users.uuid (blocks a user's permanent delete, as their IDRs do)
--     deleted_at     TIMESTAMPTZ NULL   soft delete, as on idrs and users
--     deleted_by     UUID NULL          FK -> users.uuid ON DELETE SET NULL, as users.deleted_by
--     uq_properties_project_bbl          UNIQUE (project_id, bbl) WHERE deleted_at IS NULL
--
-- A soft-deleted property frees its BBL; the unique index also serves lookups by project, so there is no
-- separate project index. No existing table or row changes.
--
-- Run in the Supabase SQL editor, after 028, BEFORE deploying the R0a code (its routes name the table).
-- Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The table doesn't exist yet:
--   SELECT to_regclass('icid.properties');
--   Expected: NULL

-- 2. Migration 028 is in (users.deleted_at exists):
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'users' AND column_name = 'deleted_at';
--   Expected: one row

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE icid.properties (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     TEXT NOT NULL REFERENCES icid.projects(project_id),
    bbl            TEXT NOT NULL,
    address        TEXT NOT NULL,
    property_type  TEXT NOT NULL,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at     TIMESTAMPTZ,
    deleted_by     UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_properties_type CHECK (property_type IN ('1_3_family', '4_plus_family', 'expedited'))
);

CREATE UNIQUE INDEX uq_properties_project_bbl ON icid.properties (project_id, bbl) WHERE deleted_at IS NULL;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The table is there and empty:
--   SELECT count(*) FROM icid.properties;
--   Expected: 0

-- B. The unique index is partial:
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'uq_properties_project_bbl';
--   Expected: CREATE UNIQUE INDEX uq_properties_project_bbl ON icid.properties USING btree (project_id, bbl)
--             WHERE (deleted_at IS NULL)

-- C. The constraints:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.properties'::regclass ORDER BY conname;
--   Expected: chk_properties_type, properties_created_by_fkey, properties_deleted_by_fkey (ON DELETE SET NULL),
--             properties_pkey, properties_project_id_fkey

-- ============================================================
-- ROLLBACK (drops every property)
-- ============================================================

-- DROP TABLE icid.properties;
