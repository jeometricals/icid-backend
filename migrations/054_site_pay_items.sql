-- migrations/054_site_pay_items.sql
--
-- Slice V.
--   * V1: icid.idr_report_site_pay_items, pay items an inspector enters by hand on an imported site, billed at approval
--     as quantity rows of source 'from_sites' (chk_quantities_source widened). A row goes with its site (ON DELETE
--     CASCADE), and so with its report and its IDR. contract_item_id has no delete action: the admin pay-item deletes
--     refuse an item in use (409). One more foreign key to users (added_by; count_user_references counts it).
--   * V3: an attachment's description is optional (NULL when blank); a description given is still not blank and at
--     most 2000 characters.
--
-- Additive, and loosens two rules: nothing in the live data is touched. Run with 055, before deploying the Slice V code.
-- Safe to run twice.
--
-- Rollback (after rolling the code back, and once no quantity row is 'from_sites' and no description is NULL):
-- DROP TABLE icid.idr_report_site_pay_items; restore chk_quantities_source without 'from_sites';
-- ALTER TABLE icid.report_attachments ALTER COLUMN attachment_description SET NOT NULL and restore its CHECK.

BEGIN;

CREATE TABLE IF NOT EXISTS icid.idr_report_site_pay_items (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_site_id   UUID NOT NULL REFERENCES icid.idr_report_sites(id) ON DELETE CASCADE,
    contract_item_id UUID NOT NULL REFERENCES icid.contract_items(contract_item_id),
    quantity         NUMERIC NOT NULL CHECK (quantity > 0),
    note             TEXT CHECK (note IS NULL OR char_length(note) <= 2000),
    added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by         UUID NOT NULL REFERENCES icid.users(uuid)
);
CREATE INDEX IF NOT EXISTS idx_idr_report_site_pay_items_site ON icid.idr_report_site_pay_items (report_site_id);
CREATE INDEX IF NOT EXISTS idx_idr_report_site_pay_items_item ON icid.idr_report_site_pay_items (contract_item_id);

ALTER TABLE icid.quantities DROP CONSTRAINT IF EXISTS chk_quantities_source;
ALTER TABLE icid.quantities ADD CONSTRAINT chk_quantities_source
    CHECK (source IN ('manual', 'from_flags', 'from_segments', 'from_sites'));

ALTER TABLE icid.report_attachments ALTER COLUMN attachment_description DROP NOT NULL;
ALTER TABLE icid.report_attachments DROP CONSTRAINT IF EXISTS chk_report_attachments_description;
ALTER TABLE icid.report_attachments ADD CONSTRAINT chk_report_attachments_description
    CHECK (attachment_description IS NULL
        OR (btrim(attachment_description) <> '' AND char_length(attachment_description) <= 2000));

COMMIT;
