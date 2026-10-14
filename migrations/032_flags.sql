-- migrations/032_flags.sql
-- Flags (Slice R1b, 2026-10-14): the marked pieces of sidewalk on a PIR, one row each, with their shape, the DOT
-- survey's classification, and the quantity they come to.
--
--   icid.flags   (new)
--     id                              UUID PK
--     pir_id                          UUID NOT NULL      FK -> pirs.id (no cascade: a flag is a record of its own)
--     display_order                   INT NOT NULL       >= 0; not unique (the list sorts by it, then created_at)
--     added_in_field                  BOOLEAN NOT NULL   true for a flag the inspector added on site (no PIR mark)
--     shape                           TEXT NOT NULL      rectangle | trapezoid | triangle | polygon | arc
--     dimensions                      JSONB NOT NULL     per shape, checked by the API (feet)
--     sketch_x, sketch_y              REAL NULL          where it sits on the sketch
--     rotation                        REAL NOT NULL      degrees, default 0
--     surveyed_mark                   TEXT NOT NULL      number_only | cross | cross_with_number | tick | none
--     surveyed_is_assessable          BOOLEAN NOT NULL
--     surveyed_non_assessable_reason  TEXT NULL          required when not assessable, empty when assessable
--     surveyed_thickness              TEXT NOT NULL      4in | 7in | 7in_as_4in
--     is_constructed                  BOOLEAN NOT NULL   default false; set by a later IDR-claim slice, not the API
--     pay_item_id                     UUID NULL          FK -> contract_items.contract_item_id ON DELETE SET NULL
--     computed_quantity               NUMERIC NOT NULL   the shape's area, square feet, computed by the API
--     reported_quantity               NUMERIC NULL       an override; NULL means the computed quantity stands
--     quantity_delta_reason           TEXT NULL
--     created_at, updated_at
--     created_by                      UUID NOT NULL      FK -> users.uuid
--     deleted_at                      TIMESTAMPTZ NULL   soft delete, as on PIRs
--     deleted_by                      UUID NULL          FK -> users.uuid ON DELETE SET NULL
--     idx_flags_pir, idx_flags_pay_item (both partial, live flags only)
--
-- pay_item_id is SET NULL on delete because pay items are hard-deleted by the admin pay-item routes: a flag keeps
-- its geometry and quantity and loses only its mapping. No existing table or row changes.
--
-- Run in the Supabase SQL editor, after 031, BEFORE deploying the R1b code (its routes, and the user-reference
-- count behind permanent user delete, name the table). Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The table doesn't exist yet, and 031's does:
--   SELECT to_regclass('icid.flags'), to_regclass('icid.pirs');
--   Expected: NULL, pirs

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE icid.flags (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_id                          UUID NOT NULL REFERENCES icid.pirs(id),
    display_order                   INT NOT NULL,
    added_in_field                  BOOLEAN NOT NULL DEFAULT false,
    shape                           TEXT NOT NULL,
    dimensions                      JSONB NOT NULL,
    sketch_x                        REAL,
    sketch_y                        REAL,
    rotation                        REAL NOT NULL DEFAULT 0,
    surveyed_mark                   TEXT NOT NULL,
    surveyed_is_assessable          BOOLEAN NOT NULL,
    surveyed_non_assessable_reason  TEXT,
    surveyed_thickness              TEXT NOT NULL,
    is_constructed                  BOOLEAN NOT NULL DEFAULT false,
    pay_item_id                     UUID REFERENCES icid.contract_items(contract_item_id) ON DELETE SET NULL,
    computed_quantity               NUMERIC NOT NULL,
    reported_quantity               NUMERIC,
    quantity_delta_reason           TEXT,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                      UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at                      TIMESTAMPTZ,
    deleted_by                      UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_flags_display_order CHECK (display_order >= 0),
    CONSTRAINT chk_flags_shape CHECK (shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc')),
    CONSTRAINT chk_flags_surveyed_mark
        CHECK (surveyed_mark IN ('number_only', 'cross', 'cross_with_number', 'tick', 'none')),
    CONSTRAINT chk_flags_non_assessable_reason CHECK (surveyed_non_assessable_reason IS NULL OR
        surveyed_non_assessable_reason IN ('tree_damage', 'city_tree', 'transition', 'no_driveway', 'hydrant',
                                           'fronting_city', 'utility', 'settlement', 'other')),
    CONSTRAINT chk_flags_thickness CHECK (surveyed_thickness IN ('4in', '7in', '7in_as_4in')),
    CONSTRAINT chk_flags_reported_quantity CHECK (reported_quantity IS NULL OR reported_quantity > 0)
);

CREATE INDEX idx_flags_pir ON icid.flags (pir_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_flags_pay_item ON icid.flags (pay_item_id) WHERE deleted_at IS NULL AND pay_item_id IS NOT NULL;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The table is there and empty:
--   SELECT count(*) FROM icid.flags;
--   Expected: 0

-- B. The foreign keys:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.flags'::regclass AND contype = 'f' ORDER BY conname;
--   Expected: created_by (no action), deleted_by ON DELETE SET NULL, pay_item_id REFERENCES
--             icid.contract_items(contract_item_id) ON DELETE SET NULL, pir_id (no action)

-- C. The two partial indexes:
--   SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'icid' AND tablename = 'flags' ORDER BY indexname;
--   Expected: flags_pkey, idx_flags_pay_item (... WHERE deleted_at IS NULL AND pay_item_id IS NOT NULL),
--             idx_flags_pir (... WHERE deleted_at IS NULL)

-- ============================================================
-- ROLLBACK (drops every flag)
-- ============================================================

-- DROP TABLE icid.flags;
