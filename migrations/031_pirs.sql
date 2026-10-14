-- migrations/031_pirs.sql
-- PIRs (Slice R1a, 2026-10-14): a property's digitized PIR, the DOT survey sheet an inspector signed off,
-- one live PIR per property.
--
--   icid.pirs   (new)
--     id                   UUID PK
--     property_id          UUID NOT NULL      FK -> properties.id (no cascade: a PIR is a record of its own)
--     source_image_url     TEXT NULL          the scanned sheet; not written until R1d
--     source_image_scale   REAL NULL          likewise
--     inspector_initials   TEXT NOT NULL
--     inspector_sign_date  DATE NOT NULL
--     digitized_by         UUID NOT NULL      FK -> users.uuid; the admin who created the row
--     digitized_at         TIMESTAMPTZ NOT NULL
--     as_built_signed_by   UUID NULL          FK -> users.uuid ON DELETE SET NULL; set by a later slice's sign route
--     as_built_signed_at   TIMESTAMPTZ NULL
--     created_at, updated_at
--     deleted_at           TIMESTAMPTZ NULL   soft delete, as on properties
--     deleted_by           UUID NULL          FK -> users.uuid ON DELETE SET NULL
--     uq_pirs_property                        UNIQUE (property_id) WHERE deleted_at IS NULL
--
-- A soft-deleted PIR frees its property, so a replacement can be created while the old one is kept. No
-- existing table or row changes.
--
-- Run in the Supabase SQL editor, after 030, BEFORE deploying the R1a code (its routes, and the user-reference
-- count behind permanent user delete, name the table). Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The table doesn't exist yet, and 030's do:
--   SELECT to_regclass('icid.pirs'), to_regclass('icid.task_orders');
--   Expected: NULL, task_orders

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE icid.pirs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id          UUID NOT NULL REFERENCES icid.properties(id),
    source_image_url     TEXT,
    source_image_scale   REAL,
    inspector_initials   TEXT NOT NULL,
    inspector_sign_date  DATE NOT NULL,
    digitized_by         UUID NOT NULL REFERENCES icid.users(uuid),
    digitized_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_built_signed_by   UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    as_built_signed_at   TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    deleted_by           UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);

CREATE UNIQUE INDEX uq_pirs_property ON icid.pirs (property_id) WHERE deleted_at IS NULL;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The table is there and empty:
--   SELECT count(*) FROM icid.pirs;
--   Expected: 0

-- B. The unique index is partial:
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'uq_pirs_property';
--   Expected: CREATE UNIQUE INDEX uq_pirs_property ON icid.pirs USING btree (property_id) WHERE (deleted_at IS NULL)

-- C. The foreign keys:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.pirs'::regclass AND contype = 'f' ORDER BY conname;
--   Expected: as_built_signed_by and deleted_by ON DELETE SET NULL; digitized_by and property_id with no action

-- ============================================================
-- ROLLBACK (drops every PIR)
-- ============================================================

-- DROP TABLE icid.pirs;
