-- migrations/026_contract_items_own_columns.sql
-- Contract items carry their own item number, description and unit (Slice P0a, 2026-10-13), so an
-- admin can enter a project's Schedule of Bid Items as its bid document has it, without touching the
-- shared spec_items catalog.
--
--   icid.contract_items   (changed)
--     item_no         TEXT NOT NULL   new: the pay item's number, e.g. '4.13 AAS'; copied from the row's spec item
--     description     TEXT NOT NULL   new: copied from the row's spec item
--     unit            TEXT NOT NULL   new: copied from the spec item's pay_unit, as it is ('L.F.', 'Ton')
--     spec_section    TEXT NULL       new: copied from the row's spec item; optional from here on
--     spec_item_id    UUID NULL       was NOT NULL; a row an admin creates has none
--     bid_quantity    NUMERIC         was NUMERIC(12,2); the API takes up to 10 decimal places
--     bid_unit_price  NUMERIC         was NUMERIC(12,2); the API takes up to 6 decimal places
--     UNIQUE (project_id, item_no, budget_code)   uq_contract_items_project_item_budget
--       replaces UNIQUE (project_id, spec_item_id, budget_code), uq_contract_items_project_spec_budget
--
-- icid.spec_items is not changed and nothing is removed from it: existing rows keep their
-- spec_item_id, and the table stays until it is cleaned up. No contract item is added or removed.
--
-- The code deployed before this migration keeps working after it (it still finds every existing
-- row's spec item). The P0a code needs the new columns, so run this before deploying it.
--
-- Run in the Supabase SQL editor, after 025. Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The new columns don't exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'contract_items'
--     AND column_name IN ('item_no', 'description', 'unit', 'spec_section');
--   Expected: no rows

-- 2. How many contract items there are (23, all on HWS0023, on 2026-10-13); note it for check B:
--   SELECT project_id, count(*) FROM icid.contract_items GROUP BY project_id;

-- 3. Every contract item has its spec item (the backfill reads it):
--   SELECT count(*) FROM icid.contract_items ci
--   LEFT JOIN icid.spec_items si ON si.spec_item_id = ci.spec_item_id
--   WHERE si.spec_item_id IS NULL;
--   Expected: 0

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.contract_items
    ADD COLUMN item_no      TEXT,
    ADD COLUMN description  TEXT,
    ADD COLUMN unit         TEXT,
    ADD COLUMN spec_section TEXT;

UPDATE icid.contract_items ci
SET item_no      = si.item_no,
    description  = si.description,
    unit         = si.pay_unit,
    spec_section = si.spec_section
FROM icid.spec_items si
WHERE si.spec_item_id = ci.spec_item_id;

-- Fails, and rolls everything back, if a row found no spec item
ALTER TABLE icid.contract_items
    ALTER COLUMN item_no SET NOT NULL,
    ALTER COLUMN description SET NOT NULL,
    ALTER COLUMN unit SET NOT NULL,
    ALTER COLUMN spec_item_id DROP NOT NULL,
    ALTER COLUMN bid_quantity TYPE NUMERIC,
    ALTER COLUMN bid_unit_price TYPE NUMERIC;

ALTER TABLE icid.contract_items
    DROP CONSTRAINT uq_contract_items_project_spec_budget,
    ADD CONSTRAINT uq_contract_items_project_item_budget UNIQUE (project_id, item_no, budget_code);

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The columns: twelve, with spec_item_id and spec_section nullable and the two amounts unbounded:
--   SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable
--   FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'contract_items' ORDER BY ordinal_position;
--   Expected: twelve rows; item_no, description and unit NOT NULL; spec_item_id and spec_section
--   nullable; bid_quantity and bid_unit_price numeric with NULL precision and scale

-- B. Every row was filled from its spec item, and none was added or lost:
--   SELECT count(*) AS items,
--          count(*) FILTER (WHERE ci.item_no = si.item_no AND ci.description = si.description
--                             AND ci.unit = si.pay_unit AND ci.spec_section = si.spec_section) AS filled
--   FROM icid.contract_items ci
--   JOIN icid.spec_items si ON si.spec_item_id = ci.spec_item_id;
--   Expected: both equal the count from pre-check 2

-- C. The unique constraint was swapped:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.contract_items'::regclass AND contype = 'u';
--   Expected: one row, uq_contract_items_project_item_budget, UNIQUE (project_id, item_no, budget_code)

-- D. The amounts kept their values:
--   SELECT item_no, budget_code, bid_quantity, bid_unit_price FROM icid.contract_items
--   WHERE project_id = 'HWS0023' AND item_no = '4.13 AAS' ORDER BY budget_code;
--   Expected: 12345 | 9500.00 | 14.50 and 67890 | 2000.00 | 19.00

-- ============================================================
-- ROLLBACK (only while no contract item has been created through the API: such a row has no
-- spec_item_id, and its amounts may not fit NUMERIC(12,2))
-- ============================================================

-- BEGIN;
-- ALTER TABLE icid.contract_items
--     DROP CONSTRAINT uq_contract_items_project_item_budget,
--     ADD CONSTRAINT uq_contract_items_project_spec_budget UNIQUE (project_id, spec_item_id, budget_code);
-- ALTER TABLE icid.contract_items
--     ALTER COLUMN spec_item_id SET NOT NULL,
--     ALTER COLUMN bid_quantity TYPE NUMERIC(12,2),
--     ALTER COLUMN bid_unit_price TYPE NUMERIC(12,2),
--     DROP COLUMN item_no,
--     DROP COLUMN description,
--     DROP COLUMN unit,
--     DROP COLUMN spec_section;
-- COMMIT;
