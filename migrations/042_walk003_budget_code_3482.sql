-- migrations/042_walk003_budget_code_3482.sql
--
-- Data fix: Walk003 (HWS0023, approved) has one pay item an inspector filed under budget code '3482', which no
-- contract item has, so its 2 TN of 4.02 CA counts as unbudgeted. HWS0023's 4.02 CA is under 'HW-200(S)'. Move the
-- item, in its GEN report (pay item ddce3d68-a0c6-4e52-a4f1-ab68c37dd4cd) and in its manual quantity row, to
-- 'HW-200(S)'. A one-off data migration: no schema change, so schema.sql is unchanged. No idr_field_edits row: this
-- is test-data cleanup before the pilot, not a reviewer's edit.
--
-- As of 2026-10-19 '3482' appears nowhere else: not in icid.contract_items (SE384's four 4.02 CA rows are under
-- legitimate codes and are untouched), and only in this report and this quantity row. Amounts and units are untouched.
--
-- Rollback: set both back to '3482' by hand. Safe to run twice: the second run changes nothing and the end-state check
-- still passes.

BEGIN;

-- 0. Guard: Walk003 is HWS0023's, approved, and HW-200(S) has a 4.02 CA contract item
DO $$
BEGIN
  IF (SELECT count(*) FROM icid.idrs WHERE project_id = 'HWS0023' AND idr_number = 'Walk003'
        AND status = 'approved' AND deleted_at IS NULL) <> 1 THEN
    RAISE EXCEPTION '042: expected one approved IDR Walk003 on HWS0023';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM icid.contract_items
                 WHERE project_id = 'HWS0023' AND item_no = '4.02 CA' AND budget_code = 'HW-200(S)') THEN
    RAISE EXCEPTION '042: HWS0023 has no 4.02 CA contract item under HW-200(S)';
  END IF;
END $$;

-- 1. The report's pay item
UPDATE icid.idr_reports r
SET report_data = jsonb_set(
      r.report_data, '{payItems}',
      (SELECT jsonb_agg(CASE WHEN item->>'id' = 'ddce3d68-a0c6-4e52-a4f1-ab68c37dd4cd' AND item->>'budgetCode' = '3482'
                             THEN jsonb_set(item, '{budgetCode}', '"HW-200(S)"') ELSE item END ORDER BY ord)
       FROM jsonb_array_elements(r.report_data->'payItems') WITH ORDINALITY AS t(item, ord)))
FROM icid.idrs i
WHERE i.idr_id = r.idr_id
  AND i.project_id = 'HWS0023' AND i.idr_number = 'Walk003' AND i.deleted_at IS NULL
  AND r.report_type = 'GEN'
  AND r.report_data->'payItems' @> '[{"id": "ddce3d68-a0c6-4e52-a4f1-ab68c37dd4cd", "budgetCode": "3482"}]';

-- 2. Its quantity row
UPDATE icid.quantities q
SET budget_code = 'HW-200(S)'
FROM icid.idrs i
WHERE i.idr_id = q.idr_id
  AND i.project_id = 'HWS0023' AND i.idr_number = 'Walk003' AND i.deleted_at IS NULL
  AND q.source = 'manual' AND q.pay_item_ref = '4.02 CA' AND q.budget_code = '3482';

-- 3. End state: '3482' is left in no report pay item and no quantity row
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM icid.quantities WHERE budget_code = '3482')
     OR EXISTS (SELECT 1 FROM icid.idr_reports WHERE report_data->'payItems' @> '[{"budgetCode": "3482"}]') THEN
    RAISE EXCEPTION '042: budget code 3482 is still present';
  END IF;
END $$;

COMMIT;
