-- migrations/056_site_pay_item_overrides.sql
--
-- Slice X.1: a site pay item can override what the site's flags and curb segments come to for a pay item in an IDR.
-- overrides_auto = true makes the row's quantity replace that automatic quantity (higher or lower), and it may be 0;
-- an ordinary row (overrides_auto = false) still adds to it and stays above 0. One override per site and pay item.
-- Approval still claims the overridden flags and segments, bills the override as a from_sites row in their place, and
-- drops an override when it claims none of them (the code's rule; nothing here enforces it).
--
-- Touches live data: the 2 existing rows of icid.idr_report_site_pay_items (both on 152 23 ST, in the 10/12 draft
-- IDR) take overrides_auto = false; their quantities are unchanged. Run before deploying the Slice X.1 code. Safe to
-- run twice.
--
-- Rollback (after rolling the code back, and deleting any override rows first, since 0 breaks the old CHECK):
--   DROP INDEX icid.uq_idr_report_site_pay_items_one_override;
--   ALTER TABLE icid.idr_report_site_pay_items DROP CONSTRAINT idr_report_site_pay_items_quantity_check,
--       ADD CONSTRAINT idr_report_site_pay_items_quantity_check CHECK (quantity > 0), DROP COLUMN overrides_auto;

BEGIN;

ALTER TABLE icid.idr_report_site_pay_items
    ADD COLUMN IF NOT EXISTS overrides_auto BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE icid.idr_report_site_pay_items DROP CONSTRAINT IF EXISTS idr_report_site_pay_items_quantity_check;
ALTER TABLE icid.idr_report_site_pay_items
    ADD CONSTRAINT idr_report_site_pay_items_quantity_check CHECK (quantity > 0 OR (overrides_auto AND quantity >= 0));

CREATE UNIQUE INDEX IF NOT EXISTS uq_idr_report_site_pay_items_one_override
    ON icid.idr_report_site_pay_items (report_site_id, contract_item_id) WHERE overrides_auto;

COMMIT;
