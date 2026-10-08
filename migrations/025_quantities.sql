-- migrations/025_quantities.sql
-- The quantities table (Slice L0, 2026-10-08): the pay-item quantities of approved IDRs, one row per
-- pay item, kept apart from the reports so progress and as-built reports can read them with plain
-- queries.
--
--   icid.quantities   (new)
--     quantity_id     UUID PK
--     project_id      TEXT          the IDR's project
--     idr_id          UUID          the IDR the quantity comes from; its rows go when the IDR is removed
--     report_date     DATE          the IDR's work date (idrs.report_date)
--     reporter_uuid   UUID          the IDR's inspector (idrs.reporter_uuid)
--     report_type     TEXT          the type of the report the pay item is on: GEN, SWCB or AC
--     pay_item_ref    TEXT NULL     the item number as the report stores it (payItems[].itemNo); NULL for
--                                   an item without one, e.g. one a reviewer added by its description
--     budget_code     TEXT NULL     payItems[].budgetCode
--     description     TEXT NULL     payItems[].description
--     amount          NUMERIC       payItems[].payQuantity, parsed
--     unit            TEXT NULL     payItems[].unit, in its short form (LF, SF, CY, SY, TN, EA, LS)
--     created_at      TIMESTAMPTZ
--
-- project_id, report_date and reporter_uuid are copies of the IDR's own, so a date range or an
-- inspector can be queried without a join. An IDR's rows are always replaced as a set (delete, then
-- insert), so the copies never drift from the IDR that was approved.
--
-- No unique constraint: the same item and budget code can be on one IDR more than once.
--
-- Nothing writes to the table yet (the API fills it at final approval from L1 on), and there is no
-- backfill here: scripts/backfill_quantities.py (L1) fills it for the IDRs already approved.
--
-- No existing table or row changes. Run in the Supabase SQL editor, after 023 (there is no 024).
-- Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The table doesn't exist yet:
--   SELECT to_regclass('icid.quantities');
--   Expected: NULL

-- 2. gen_random_uuid() is callable (idr_audit and idr_field_edits already default their ids with it):
--   SELECT gen_random_uuid();
--   Expected: a UUID

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE icid.quantities (
    quantity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     TEXT NOT NULL REFERENCES icid.projects(project_id),
    idr_id         UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    report_date    DATE NOT NULL,
    reporter_uuid  UUID NOT NULL,
    report_type    TEXT NOT NULL,
    pay_item_ref   TEXT,
    budget_code    TEXT,
    description    TEXT,
    amount         NUMERIC NOT NULL,
    unit           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_quantities_project_date ON icid.quantities(project_id, report_date);
CREATE INDEX idx_quantities_project_item ON icid.quantities(project_id, pay_item_ref);
CREATE INDEX idx_quantities_project_budget ON icid.quantities(project_id, budget_code);
CREATE INDEX idx_quantities_idr ON icid.quantities(idr_id);

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The table is there, empty, with its twelve columns:
--   SELECT column_name, data_type, is_nullable FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'quantities' ORDER BY ordinal_position;
--   Expected: twelve rows; pay_item_ref, budget_code, description and unit nullable, the rest not
--   SELECT count(*) FROM icid.quantities;
--   Expected: 0

-- B. Its four indexes, and the primary key's:
--   SELECT indexname FROM pg_indexes WHERE schemaname = 'icid' AND tablename = 'quantities' ORDER BY indexname;
--   Expected: idx_quantities_idr, idx_quantities_project_budget, idx_quantities_project_date,
--             idx_quantities_project_item, quantities_pkey

-- C. Its two foreign keys:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.quantities'::regclass AND contype = 'f' ORDER BY conname;
--   Expected: quantities_idr_id_fkey (... REFERENCES icid.idrs(idr_id) ON DELETE CASCADE) and
--             quantities_project_id_fkey (... REFERENCES icid.projects(project_id))
