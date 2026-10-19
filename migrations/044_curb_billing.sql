-- migrations/044_curb_billing.sql
--
-- Curb billing (Slice CB1): curb segments are claimed by IDRs and billed, as flags are (R1f).
--   * segments.claimed_by_idr_id and constructed_on: set when an approved IDR claims the segment, cleared when that IDR
--     is unlocked or deleted. CHECK chk_segments_claim_consistent keeps the two together.
--   * icid.idr_segment_attributions: an IDR's overrides of its default segments (exclude one of its task order's sites'
--     segments, include one from elsewhere in its project), as idr_flag_attributions does for flags.
--   * quantities.source takes 'from_segments': the claimed segments' lengths summed per pay item at approval.
--   * HWS0023 gets two contract items, for the curb kinds its catalog had no line for: 4.08 AB depressed concrete curb
--     ($55.00/LF, 500 LF) and 4.08 GA granite curb ($150.00/LF, 400 LF), both under HW-200(S).
--
-- The code that reads segments selects the new columns, so run this before deploying it. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.idr_segment_attributions; ALTER TABLE icid.segments DROP
-- CONSTRAINT chk_segments_claim_consistent, DROP COLUMN claimed_by_idr_id, DROP COLUMN constructed_on; restore
-- chk_quantities_source to ('manual', 'from_flags') once no from_segments row is left; delete the two contract items.

BEGIN;

ALTER TABLE icid.segments ADD COLUMN IF NOT EXISTS claimed_by_idr_id UUID REFERENCES icid.idrs(idr_id);
ALTER TABLE icid.segments ADD COLUMN IF NOT EXISTS constructed_on DATE;
ALTER TABLE icid.segments DROP CONSTRAINT IF EXISTS chk_segments_claim_consistent;
ALTER TABLE icid.segments ADD CONSTRAINT chk_segments_claim_consistent
    CHECK ((claimed_by_idr_id IS NULL) = (constructed_on IS NULL));
CREATE INDEX IF NOT EXISTS idx_segments_claimed_by_idr ON icid.segments (claimed_by_idr_id)
    WHERE claimed_by_idr_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS icid.idr_segment_attributions (
    idr_id      UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    segment_id  UUID NOT NULL REFERENCES icid.segments(segment_id) ON DELETE CASCADE,
    action      TEXT NOT NULL CHECK (action IN ('exclude', 'include')),
    set_by      UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (idr_id, segment_id)
);
CREATE INDEX IF NOT EXISTS idx_idr_segment_attributions_segment ON icid.idr_segment_attributions (segment_id);

ALTER TABLE icid.quantities DROP CONSTRAINT IF EXISTS chk_quantities_source;
ALTER TABLE icid.quantities ADD CONSTRAINT chk_quantities_source
    CHECK (source IN ('manual', 'from_flags', 'from_segments'));

INSERT INTO icid.contract_items (project_id, item_no, budget_code, description, unit, bid_quantity, bid_unit_price)
VALUES ('HWS0023', '4.08 AB', 'HW-200(S)', 'Depressed Concrete Curb (18" Deep)', 'LF', 500, 55.00),
       ('HWS0023', '4.08 GA', 'HW-200(S)', 'Granite Curb (18" Deep)', 'LF', 400, 150.00)
ON CONFLICT (project_id, item_no, budget_code) DO NOTHING;

COMMIT;
