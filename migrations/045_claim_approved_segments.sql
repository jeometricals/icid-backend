-- migrations/045_claim_approved_segments.sql
--
-- Curb billing, data only (no schema change; run after 044):
--   1. HWS0023's live curb segments get the pay item of their kind, where they have none: curb_concrete -> 4.08 AA
--      (Concrete Curb), curb_concrete_depressed -> 4.08 AB (Depressed Concrete Curb), curb_granite -> 4.08 GA (Granite
--      Curb), all HW-200(S). As of 2026-10-19 that is 17 segments: 14 concrete, 2 depressed, 1 granite.
--   2. The IDRs approved before curb billing existed (IDR-001 and Walk004, HWS0023) claim the segments approval would
--      have claimed: the live segments on their task order's properties, none excluded, none claimed by another IDR,
--      constructed on their report date; and get their from_segments quantity rows, as approval writes them.
--
-- Rollback: delete the two IDRs' from_segments rows, clear the segments' claims (claimed_by_idr_id, constructed_on)
-- and, if wanted, their pay_item_id. Safe to run twice: the second run changes nothing (claims and rows are only added
-- where there are none).

BEGIN;

-- 0. Guard: the three pay items exist
DO $$
BEGIN
  IF (SELECT count(*) FROM icid.contract_items WHERE project_id = 'HWS0023' AND budget_code = 'HW-200(S)'
        AND item_no IN ('4.08 AA', '4.08 AB', '4.08 GA')) <> 3 THEN
    RAISE EXCEPTION '045: HWS0023 needs 4.08 AA, 4.08 AB and 4.08 GA under HW-200(S) (run 044 first)';
  END IF;
END $$;

-- 1. Pay items by kind
UPDATE icid.segments s
SET pay_item_id = ci.contract_item_id, updated_at = now()
FROM icid.pir_pages pg, icid.pirs pir, icid.properties p, icid.contract_items ci
WHERE pg.pir_page_id = s.pir_page_id AND pir.id = pg.pir_id AND p.id = pir.property_id
  AND p.project_id = 'HWS0023' AND s.deleted_at IS NULL AND s.pay_item_id IS NULL
  AND ci.project_id = 'HWS0023' AND ci.budget_code = 'HW-200(S)'
  AND ci.item_no = CASE s.segment_type
                     WHEN 'curb_concrete' THEN '4.08 AA'
                     WHEN 'curb_concrete_depressed' THEN '4.08 AB'
                     WHEN 'curb_granite' THEN '4.08 GA'
                   END;

-- 2. Claims, as approval makes them
WITH approved AS (
    SELECT i.idr_id, i.report_date, i.task_order_id
    FROM icid.idrs i
    WHERE i.project_id = 'HWS0023' AND i.idr_number IN ('IDR-001', 'Walk004')
      AND i.status = 'approved' AND i.deleted_at IS NULL
)
UPDATE icid.segments s
SET claimed_by_idr_id = a.idr_id, constructed_on = a.report_date
FROM approved a, icid.task_order_properties tp, icid.pirs pir, icid.pir_pages pg, icid.properties p
WHERE tp.task_order_id = a.task_order_id AND pir.property_id = tp.property_id AND pg.pir_id = pir.id
  AND s.pir_page_id = pg.pir_page_id AND p.id = pir.property_id
  AND s.deleted_at IS NULL AND pg.deleted_at IS NULL AND pir.deleted_at IS NULL AND p.deleted_at IS NULL
  AND s.claimed_by_idr_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM icid.idr_segment_attributions x
                  WHERE x.idr_id = a.idr_id AND x.segment_id = s.segment_id AND x.action = 'exclude');

-- 3. Their from_segments quantity rows, where they have none
INSERT INTO icid.quantities (project_id, idr_id, report_date, reporter_uuid, report_type, pay_item_ref, budget_code,
                             description, amount, unit, source)
SELECT i.project_id, i.idr_id, i.report_date, i.reporter_uuid, 'PIR_SEGMENTS', ci.item_no, ci.budget_code,
       ci.description, round(sum(s.length_ft), 2), 'LF', 'from_segments'
FROM icid.idrs i
JOIN icid.segments s ON s.claimed_by_idr_id = i.idr_id
JOIN icid.contract_items ci ON ci.contract_item_id = s.pay_item_id
WHERE i.project_id = 'HWS0023' AND i.idr_number IN ('IDR-001', 'Walk004') AND i.status = 'approved'
  AND upper(regexp_replace(ci.unit, '[^A-Za-z]', '', 'g')) = 'LF' AND s.length_ft IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM icid.quantities q WHERE q.idr_id = i.idr_id AND q.source = 'from_segments')
GROUP BY i.project_id, i.idr_id, i.report_date, i.reporter_uuid, ci.contract_item_id;

-- 4. End state: every live HWS0023 curb segment has a pay item
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM icid.segments s
    JOIN icid.pir_pages pg ON pg.pir_page_id = s.pir_page_id
    JOIN icid.pirs pir ON pir.id = pg.pir_id
    JOIN icid.properties p ON p.id = pir.property_id
    WHERE p.project_id = 'HWS0023' AND s.deleted_at IS NULL AND s.segment_type LIKE 'curb%' AND s.pay_item_id IS NULL
      AND s.segment_type IN ('curb_concrete', 'curb_concrete_depressed', 'curb_granite')
  ) THEN
    RAISE EXCEPTION '045: a live HWS0023 curb segment is still without a pay item';
  END IF;
END $$;

COMMIT;
