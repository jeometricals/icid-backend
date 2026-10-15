-- migrations/037_flag_idr_attribution.sql
--
-- R1f: the flag -> IDR claim model, an IDR's task order, and per-IDR attribution overrides.
--
-- NEW COLUMNS:
--   idrs.task_order_id       — UUID nullable, FK task_orders(id), no action. With one, an IDR's flags default to the
--                              live flags on that task order's properties; without, to none (include is the only path).
--   flags.claimed_by_idr_id  — UUID nullable, FK idrs(idr_id), NO ACTION: the approved IDR that built the flag. A
--                              claim is released (by unlock or admin delete) before an IDR can be purged.
--   flags.constructed_on     — DATE nullable: the claiming IDR's report_date.
--   quantities.source        — 'manual' (a report's pay item; every row before 037) or 'from_flags' (an IDR's
--                              attributed flags, rolled up per pay item at approval).
--
-- NEW TABLE:
--   idr_flag_attributions    — a reviewer's (or the drafting inspector's) override of an IDR's default attribution:
--                              'exclude' drops a flag the default holds, 'include' adds one it doesn't.
--
-- CHECK:
--   chk_flags_claim_consistent — a flag is constructed, with a date, exactly when an IDR claims it.
--
-- Rollback: drop the table, the indexes, the CHECK and the four columns. Releasing every claim first (no flag is
-- constructed before 037) keeps is_constructed consistent with what it was.
--
-- Precheck (read-only): no flag is constructed yet, and none of the new names exists:
--   SELECT count(*) FILTER (WHERE is_constructed) AS constructed FROM icid.flags;              -- must be 0
--   SELECT to_regclass('icid.idr_flag_attributions');                                          -- must be NULL

BEGIN;

-- 1. An IDR's task order (its default flag scope)
ALTER TABLE icid.idrs ADD COLUMN task_order_id UUID REFERENCES icid.task_orders(id);

-- 2. The flag -> IDR claim
ALTER TABLE icid.flags ADD COLUMN claimed_by_idr_id UUID REFERENCES icid.idrs(idr_id);
ALTER TABLE icid.flags ADD COLUMN constructed_on DATE;

ALTER TABLE icid.flags ADD CONSTRAINT chk_flags_claim_consistent CHECK (
  (claimed_by_idr_id IS NULL     AND is_constructed = false AND constructed_on IS NULL)
  OR
  (claimed_by_idr_id IS NOT NULL AND is_constructed = true  AND constructed_on IS NOT NULL)
);

-- 3. Overrides of an IDR's default attribution
CREATE TABLE icid.idr_flag_attributions (
  idr_id    UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
  flag_id   UUID NOT NULL REFERENCES icid.flags(id)    ON DELETE CASCADE,
  action    TEXT NOT NULL CHECK (action IN ('exclude', 'include')),
  set_by    UUID          REFERENCES icid.users(uuid)  ON DELETE SET NULL,
  set_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (idr_id, flag_id)
);

CREATE INDEX idx_idr_flag_attributions_flag ON icid.idr_flag_attributions (flag_id);

-- 4. Read paths: an IDR's claimed flags, and the pay-item rollup
CREATE INDEX idx_flags_claimed_by_idr ON icid.flags (claimed_by_idr_id) WHERE claimed_by_idr_id IS NOT NULL;
CREATE INDEX idx_flags_scope_rollup ON icid.flags (pay_item_id, claimed_by_idr_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_idrs_task_order ON icid.idrs (task_order_id) WHERE task_order_id IS NOT NULL;

-- 5. Where a quantity row came from; every row before 037 is a report's pay item
ALTER TABLE icid.quantities ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'
  CONSTRAINT chk_quantities_source CHECK (source IN ('manual', 'from_flags'));

COMMIT;
