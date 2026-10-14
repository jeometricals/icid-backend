-- migrations/030_task_orders.sql
-- Task orders (Slice R0b, 2026-10-14): a project's task orders, each a bundle of its properties.
--
--   icid.task_orders   (new)
--     id                 UUID PK
--     project_id         TEXT NOT NULL      FK -> projects.project_id
--     task_order_number  TEXT NOT NULL      unique per project among task orders that aren't deleted
--     issued_date        DATE NULL
--     estimate_notes     TEXT NULL
--     created_at, updated_at
--     created_by         UUID NOT NULL      FK -> users.uuid
--     deleted_at         TIMESTAMPTZ NULL   soft delete, as on properties
--     deleted_by         UUID NULL          FK -> users.uuid ON DELETE SET NULL
--     uq_task_orders_project_number          UNIQUE (project_id, task_order_number) WHERE deleted_at IS NULL
--
--   icid.task_order_properties   (new; the bundle: one row per property in a task order)
--     task_order_id  UUID NOT NULL   FK -> task_orders.id ON DELETE CASCADE
--     property_id    UUID NOT NULL   FK -> properties.id ON DELETE CASCADE
--     added_at       TIMESTAMPTZ NOT NULL
--     added_by       UUID NOT NULL   FK -> users.uuid
--     PK (task_order_id, property_id); idx_task_order_properties_property on property_id
--
-- A property can be in several task orders (a re-issue is a new task order), once in each. Soft-deleting a
-- task order or a property leaves its bundle rows alone, so un-deleting either restores the bundle; only a
-- permanent delete of either side (not built yet) takes them. That both sides share a project is checked by
-- the statement that bundles, not by a constraint. No existing table or row changes.
--
-- Run in the Supabase SQL editor, after 029, BEFORE deploying the R0b code (its routes, and the user-reference
-- count behind permanent user delete, name both tables). Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. Neither table exists yet, and 029's does:
--   SELECT to_regclass('icid.task_orders'), to_regclass('icid.task_order_properties'), to_regclass('icid.properties');
--   Expected: NULL, NULL, properties

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE icid.task_orders (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         TEXT NOT NULL REFERENCES icid.projects(project_id),
    task_order_number  TEXT NOT NULL,
    issued_date        DATE,
    estimate_notes     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at         TIMESTAMPTZ,
    deleted_by         UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);

CREATE UNIQUE INDEX uq_task_orders_project_number
    ON icid.task_orders (project_id, task_order_number) WHERE deleted_at IS NULL;

CREATE TABLE icid.task_order_properties (
    task_order_id  UUID NOT NULL REFERENCES icid.task_orders(id) ON DELETE CASCADE,
    property_id    UUID NOT NULL REFERENCES icid.properties(id) ON DELETE CASCADE,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by       UUID NOT NULL REFERENCES icid.users(uuid),
    PRIMARY KEY (task_order_id, property_id)
);

CREATE INDEX idx_task_order_properties_property ON icid.task_order_properties (property_id);

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Both tables are there and empty:
--   SELECT (SELECT count(*) FROM icid.task_orders) AS task_orders,
--          (SELECT count(*) FROM icid.task_order_properties) AS bundled;
--   Expected: 0 | 0

-- B. The unique index is partial:
--   SELECT indexdef FROM pg_indexes WHERE schemaname = 'icid' AND indexname = 'uq_task_orders_project_number';
--   Expected: CREATE UNIQUE INDEX uq_task_orders_project_number ON icid.task_orders USING btree
--             (project_id, task_order_number) WHERE (deleted_at IS NULL)

-- C. The join's foreign keys cascade:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.task_order_properties'::regclass AND contype = 'f' ORDER BY conname;
--   Expected: added_by (no action), property_id ON DELETE CASCADE, task_order_id ON DELETE CASCADE

-- ============================================================
-- ROLLBACK (drops every task order and bundle)
-- ============================================================

-- BEGIN;
-- DROP TABLE icid.task_order_properties;
-- DROP TABLE icid.task_orders;
-- COMMIT;
