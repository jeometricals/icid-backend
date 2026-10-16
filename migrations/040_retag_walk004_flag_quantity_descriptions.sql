-- migrations/040_retag_walk004_flag_quantity_descriptions.sql
--
-- M1 follow-up: Walk004's from_flags quantity rows still carry SE384's pay-item descriptions (039 moved them and
-- retagged their budget codes, not their text). Give each the description of the HWS0023 contract item it matches
-- (project, item number, budget code). A one-off data migration: no schema change, so schema.sql is unchanged.
--
-- As of 2026-10-16 one row differs, in capitals only: 4.13 AAS @ HW-200(S), '4" CONCRETE SIDEWALK (UNPIGMENTED)' ->
-- '4" Concrete Sidewalk (Unpigmented)'. 73.21AC @ HW-200(P) already matches. Amounts, units and codes are untouched.
--
-- Rollback: set the description back by hand (the old text is above). Safe to run twice: the second run changes
-- nothing and the end-state check still passes.

BEGIN;

-- 0. Guard: Walk004 is HWS0023's, approved, with only from_flags rows that each match a contract item
DO $$
BEGIN
  IF (SELECT count(*) FROM icid.idrs WHERE project_id = 'HWS0023' AND idr_number = 'Walk004'
        AND status = 'approved' AND deleted_at IS NULL) <> 1 THEN
    RAISE EXCEPTION '040: expected one approved IDR Walk004 on HWS0023';
  END IF;
  IF EXISTS (
    SELECT 1 FROM icid.quantities q
    JOIN icid.idrs i ON i.idr_id = q.idr_id
    LEFT JOIN icid.contract_items ci
      ON ci.project_id = q.project_id AND ci.item_no = q.pay_item_ref AND ci.budget_code = q.budget_code
    WHERE i.project_id = 'HWS0023' AND i.idr_number = 'Walk004' AND i.deleted_at IS NULL
      AND q.source = 'from_flags' AND ci.contract_item_id IS NULL
  ) THEN
    RAISE EXCEPTION '040: a Walk004 from_flags row matches no HWS0023 contract item';
  END IF;
END $$;

-- 1. Retag
UPDATE icid.quantities q
SET description = ci.description
FROM icid.idrs i, icid.contract_items ci
WHERE i.idr_id = q.idr_id
  AND i.project_id = 'HWS0023' AND i.idr_number = 'Walk004' AND i.deleted_at IS NULL
  AND q.source = 'from_flags'
  AND ci.project_id = q.project_id AND ci.item_no = q.pay_item_ref AND ci.budget_code = q.budget_code
  AND q.description IS DISTINCT FROM ci.description;

-- 2. End state: every Walk004 from_flags row carries its contract item's description
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM icid.quantities q
    JOIN icid.idrs i ON i.idr_id = q.idr_id
    JOIN icid.contract_items ci
      ON ci.project_id = q.project_id AND ci.item_no = q.pay_item_ref AND ci.budget_code = q.budget_code
    WHERE i.project_id = 'HWS0023' AND i.idr_number = 'Walk004' AND i.deleted_at IS NULL
      AND q.source = 'from_flags' AND q.description IS DISTINCT FROM ci.description
  ) THEN
    RAISE EXCEPTION '040: a Walk004 from_flags row still differs from its contract item';
  END IF;
END $$;

COMMIT;
