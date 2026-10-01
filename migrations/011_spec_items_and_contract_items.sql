-- migrations/011_spec_items_and_contract_items.sql
-- Pay-item catalog (Slice F, 2026-10-01): the shared NYCDOT spec catalog and each
-- project's Schedule of Bid Items.
--
--   icid.spec_items       -- standard NYCDOT items, shared across projects
--     spec_item_id    UUID PK
--     item_no         TEXT NOT NULL UNIQUE   -- e.g. '4.13 AAS'
--     description     TEXT NOT NULL
--     spec_section    TEXT NOT NULL          -- e.g. '4.13'
--     pay_unit        TEXT NOT NULL          -- e.g. 'S.F.', 'Ton'
--
--   icid.contract_items   -- per-project bid items ("contract" = project for now)
--     contract_item_id  UUID PK
--     project_id        TEXT NOT NULL -> icid.projects(project_id) ON DELETE CASCADE
--     spec_item_id      UUID NOT NULL -> icid.spec_items(spec_item_id)
--     budget_code       TEXT NOT NULL
--     bid_quantity      NUMERIC(12,2) NOT NULL
--     bid_unit_price    NUMERIC(12,2) NOT NULL
--     UNIQUE (project_id, spec_item_id, budget_code)  -- the same item may sit under
--                                                     -- several budget codes in one project
--
-- Contract items are seeded only for the MVP (seed_sidewalk_pay_items.sql); there is
-- no write endpoint yet.
--
-- Idempotent: tables and indexes are created IF NOT EXISTS. Run in the Supabase SQL
-- editor. Everything between BEGIN and COMMIT is one transaction: if any statement
-- fails, nothing is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The new tables do not exist yet:
--   SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'icid' AND table_name IN ('spec_items', 'contract_items');
--   Expected: 0 rows

-- 2. The projects key is TEXT (contract_items.project_id must match it):
--   SELECT data_type FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'projects' AND column_name = 'project_id';
--   Expected: text

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS icid.spec_items (
    spec_item_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_no        TEXT NOT NULL,
    description    TEXT NOT NULL,
    spec_section   TEXT NOT NULL,
    pay_unit       TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_spec_items_item_no UNIQUE (item_no)
);

CREATE TABLE IF NOT EXISTS icid.contract_items (
    contract_item_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id        TEXT NOT NULL REFERENCES icid.projects(project_id) ON DELETE CASCADE,
    spec_item_id      UUID NOT NULL REFERENCES icid.spec_items(spec_item_id),
    budget_code       TEXT NOT NULL,
    bid_quantity      NUMERIC(12,2) NOT NULL,
    bid_unit_price    NUMERIC(12,2) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_contract_items_project_spec_budget UNIQUE (project_id, spec_item_id, budget_code)
);

CREATE INDEX IF NOT EXISTS idx_contract_items_project ON icid.contract_items(project_id);
CREATE INDEX IF NOT EXISTS idx_contract_items_spec_item ON icid.contract_items(spec_item_id);

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. Columns, types and nullability:
--   SELECT table_name, column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name IN ('spec_items', 'contract_items')
--   ORDER BY table_name, ordinal_position;
--   Expected: 7 spec_items columns and 8 contract_items columns, all is_nullable NO

-- B. Constraints:
--   SELECT conrelid::regclass AS table_name, conname, pg_get_constraintdef(oid) AS definition
--   FROM pg_constraint
--   WHERE conrelid IN ('icid.spec_items'::regclass, 'icid.contract_items'::regclass)
--   ORDER BY table_name, conname;
--   Expected: spec_items pkey + uq_spec_items_item_no; contract_items pkey, two foreign
--   keys (project_id ON DELETE CASCADE, spec_item_id) and
--   uq_contract_items_project_spec_budget

-- C. Indexes:
--   SELECT indexname FROM pg_indexes
--   WHERE schemaname = 'icid' AND tablename = 'contract_items' ORDER BY indexname;
--   Expected: contract_items_pkey, idx_contract_items_project, idx_contract_items_spec_item,
--   uq_contract_items_project_spec_budget
