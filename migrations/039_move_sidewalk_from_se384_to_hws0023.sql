-- migrations/039_move_sidewalk_from_se384_to_hws0023.sql
--
-- M1 (v2): move the sidewalk / ped-ramp test data from SE384 to HWS0023, and standardize HWS0023's budget codes on
-- HW-200(S) and HW-200(P). A one-off data migration: no schema change, so schema.sql is unchanged.
--
-- WHAT IT DOES, in one transaction:
--   0. Guards: both projects exist, HWS0023 has no sites yet, HW-200(S)/(P) aren't there yet, Walk004 is free, every
--      SE384 flag names a known pay item, and the data is as the 2026-10-16 audit found it.
--   1. Classifies HWS0023's catalog under '12345': HW-200(P) when the description names a corner, a detectable
--      warning or a preformed unit ('corner', 'at cor.', 'detectable', 'preformed', any case), HW-200(S) otherwise.
--   2. Renames that catalog, and deletes the orphan 4.13 AAS under '67890' (nothing uses it).
--   3. Retags every copy of '12345' / '67890' in HWS0023 the same way, item by item (the catalog's code for its item
--      number, else the rule on its own description): quantity rows, report pay items (report_data), and the field
--      edits that carry a pay item or a budget code, so redlines still match the values they describe.
--   4. Seeds 73.21AC under HW-200(P) from SE384's row (price, quantity, unit, description, section).
--   5. Hard-deletes SE384's soft-deleted site history: the deleted IDR (its children cascade), soft-deleted flags,
--      areas, features and segments, the replaced pages (and their title blocks), the soft-deleted properties
--      (their bundles cascade); and HWS0023's empty task order "1".
--   6. Moves the live rows: properties, task orders, IDR 001 (renumbered Walk004; signatures and dates kept) and its
--      quantity rows (project and budget code); remaps the 15 flags to HWS0023's 4.13 AAS @ HW-200(S) and the new
--      73.21AC @ HW-200(P). PIRs, pages, title blocks, flags, areas, features, segments, bundles, the IDR's report,
--      audit rows and claims follow by foreign key: none carries a project id.
--   7. Copies SE384's DOT assessment rates to HWS0023 when HWS0023 has none (it had none), so its Construction
--      Reports and Assessment Summary price as SE384's did.
--   8. Checks the end state and refuses to finish otherwise.
--
-- NOT DONE HERE: the three replaced pages' PNGs in pir-images are deleted after the commit, by the Python helper
-- (scratch: m1_run.py), so a rolled-back transaction never loses files. HWS0023's 4.13 AAS keeps its own unit price
-- ($14.50; SE384's was $11.29); only Progress reads it.
--
-- Rollback: none (hard deletes). Take a backup first if the data matters; for the imaginary SE384 / HWS0023 it doesn't.

BEGIN;

-- 0. Guards ----------------------------------------------------------------------------------------------------------
DO $$
DECLARE
  n INT;
BEGIN
  IF (SELECT count(*) FROM icid.projects WHERE project_id IN ('SE384', 'HWS0023')) <> 2 THEN
    RAISE EXCEPTION 'M1: SE384 or HWS0023 is missing';
  END IF;
  IF EXISTS (SELECT 1 FROM icid.properties WHERE project_id = 'HWS0023') THEN
    RAISE EXCEPTION 'M1: HWS0023 already has properties (a partial earlier run?)';
  END IF;
  IF EXISTS (SELECT 1 FROM icid.contract_items WHERE project_id = 'HWS0023' AND budget_code IN ('HW-200(S)', 'HW-200(P)')) THEN
    RAISE EXCEPTION 'M1: HWS0023 already has HW-200(S) / HW-200(P) items (a partial earlier run?)';
  END IF;
  IF EXISTS (SELECT 1 FROM icid.idrs WHERE project_id = 'HWS0023' AND idr_number = 'Walk004' AND deleted_at IS NULL) THEN
    RAISE EXCEPTION 'M1: Walk004 is taken in HWS0023';
  END IF;
  SELECT count(*) INTO n
  FROM icid.flags f JOIN icid.pirs pir ON pir.id = f.pir_id JOIN icid.properties p ON p.id = pir.property_id
  JOIN icid.contract_items ci ON ci.contract_item_id = f.pay_item_id
  WHERE p.project_id = 'SE384' AND ci.item_no NOT IN ('4.13 AAS', '73.21AC', '4.01 RAG');
  IF n > 0 THEN
    RAISE EXCEPTION 'M1: % SE384 flag(s) name a pay item the mapping does not cover', n;
  END IF;
  -- The data as the audit found it
  IF (SELECT count(*) FROM icid.properties WHERE project_id = 'SE384' AND deleted_at IS NULL) <> 5
     OR (SELECT count(*) FROM icid.properties WHERE project_id = 'SE384' AND deleted_at IS NOT NULL) <> 3
     OR (SELECT count(*) FROM icid.task_orders WHERE project_id = 'SE384') <> 2
     OR (SELECT count(*) FROM icid.idrs WHERE project_id = 'SE384') <> 2
     OR (SELECT count(*) FROM icid.idrs WHERE project_id = 'SE384' AND idr_number = '001' AND status = 'approved') <> 1
     OR (SELECT count(*) FROM icid.flags f JOIN icid.pirs pir ON pir.id = f.pir_id JOIN icid.properties p ON p.id = pir.property_id
         WHERE p.project_id = 'SE384' AND f.deleted_at IS NULL) <> 15
     OR (SELECT count(*) FROM icid.contract_items WHERE project_id = 'HWS0023') <> 23 THEN
    RAISE EXCEPTION 'M1: the data has changed since the 2026-10-16 audit; re-run the inventory first';
  END IF;
END $$;

-- 1. Classification of HWS0023's catalog under '12345' -------------------------------------------------------------
CREATE TEMP TABLE m1_codes ON COMMIT DROP AS
SELECT item_no,
       CASE WHEN description ~* '(corner|at cor\.|detectable|preformed)' THEN 'HW-200(P)' ELSE 'HW-200(S)' END AS new_code
FROM icid.contract_items
WHERE project_id = 'HWS0023' AND budget_code = '12345';

-- The new code for a copy of a pay item: the catalog's code for its item number, else the rule on its description
CREATE FUNCTION pg_temp.m1_code(p_item_no TEXT, p_description TEXT) RETURNS TEXT LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    (SELECT c.new_code FROM m1_codes c WHERE c.item_no = trim(p_item_no)),
    CASE WHEN COALESCE(p_description, '') ~* '(corner|at cor\.|detectable|preformed)' THEN 'HW-200(P)' ELSE 'HW-200(S)' END)
$$;

-- A pay item (a report_data payItems entry, or a pay_item_add edit's value) with its budget code retagged
CREATE FUNCTION pg_temp.m1_item(item JSONB) RETURNS JSONB LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN jsonb_typeof(item) = 'object' AND item->>'budgetCode' IN ('12345', '67890')
              THEN jsonb_set(item, '{budgetCode}', to_jsonb(pg_temp.m1_code(item->>'itemNo', item->>'description')))
              ELSE item END
$$;

-- 2. Rename the catalog; drop the orphan ----------------------------------------------------------------------------
DELETE FROM icid.contract_items WHERE project_id = 'HWS0023' AND budget_code = '67890' AND item_no = '4.13 AAS';

UPDATE icid.contract_items ci
SET budget_code = c.new_code, updated_at = now()
FROM m1_codes c
WHERE ci.project_id = 'HWS0023' AND ci.budget_code = '12345' AND ci.item_no = c.item_no;

-- 3. Retag every copy -----------------------------------------------------------------------------------------------
UPDATE icid.quantities
SET budget_code = pg_temp.m1_code(pay_item_ref, description)
WHERE project_id = 'HWS0023' AND budget_code IN ('12345', '67890');

UPDATE icid.idr_reports r
SET report_data = jsonb_set(r.report_data, '{payItems}', (
      SELECT jsonb_agg(pg_temp.m1_item(e.item) ORDER BY e.ord)
      FROM jsonb_array_elements(r.report_data->'payItems') WITH ORDINALITY AS e(item, ord))),
    updated_at = now()
FROM icid.idrs i
WHERE i.idr_id = r.idr_id AND i.project_id = 'HWS0023'
  AND jsonb_typeof(r.report_data->'payItems') = 'array'
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(r.report_data->'payItems') item
              WHERE item->>'budgetCode' IN ('12345', '67890'));

-- A pay_item_add edit carries the whole item
UPDATE icid.idr_field_edits e
SET new_value = pg_temp.m1_item(e.new_value),
    old_value = CASE WHEN e.old_value IS NULL THEN NULL ELSE pg_temp.m1_item(e.old_value) END
FROM icid.idrs i
WHERE i.idr_id = e.idr_id AND i.project_id = 'HWS0023'
  AND (e.new_value->>'budgetCode' IN ('12345', '67890') OR e.old_value->>'budgetCode' IN ('12345', '67890'));

-- A budgetCode edit carries the code alone: retag it by the item the path names, as the report holds it now
UPDATE icid.idr_field_edits e
SET old_value = CASE WHEN e.old_value #>> '{}' IN ('12345', '67890') THEN to_jsonb(pg_temp.m1_code(item->>'itemNo', item->>'description')) ELSE e.old_value END,
    new_value = CASE WHEN e.new_value #>> '{}' IN ('12345', '67890') THEN to_jsonb(pg_temp.m1_code(item->>'itemNo', item->>'description')) ELSE e.new_value END
FROM icid.idrs i, icid.idr_reports r, jsonb_array_elements(
       CASE WHEN jsonb_typeof(r.report_data->'payItems') = 'array' THEN r.report_data->'payItems' ELSE '[]' END) item
WHERE i.idr_id = e.idr_id AND i.project_id = 'HWS0023' AND r.report_id = e.report_id
  AND e.field_path ~ '^payItems\[[^]]+\]\.budgetCode$'
  AND item->>'id' = substring(e.field_path from '^payItems\[([^]]+)\]')
  AND (e.old_value #>> '{}' IN ('12345', '67890') OR e.new_value #>> '{}' IN ('12345', '67890'));

-- 4. Seed 73.21AC under HW-200(P) -----------------------------------------------------------------------------------
INSERT INTO icid.contract_items (project_id, spec_item_id, budget_code, bid_quantity, bid_unit_price, item_no,
                                 description, unit, spec_section)
SELECT 'HWS0023', s.spec_item_id, 'HW-200(P)', s.bid_quantity, s.bid_unit_price, s.item_no, s.description, s.unit,
       s.spec_section
FROM icid.contract_items s
WHERE s.project_id = 'SE384' AND s.item_no = '73.21AC' AND s.budget_code = 'SE0200Q-CONS';

-- 5. Hard-delete the soft-deleted site history ----------------------------------------------------------------------
-- The deleted IDR: its reports, audit rows, edits, quantity rows and attributions cascade (it claims no flag)
DELETE FROM icid.idrs WHERE project_id = 'SE384' AND deleted_at IS NOT NULL;

-- Soft-deleted drawings on SE384's PIRs, then soft-deleted flags (attributions cascade)
DELETE FROM icid.non_assessable_areas na
USING icid.flags f, icid.pirs pir, icid.properties p
WHERE na.flag_id = f.id AND f.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384'
  AND (na.deleted_at IS NOT NULL OR f.deleted_at IS NOT NULL);
DELETE FROM icid.features ft
USING icid.pir_pages pg, icid.pirs pir, icid.properties p
WHERE ft.pir_page_id = pg.pir_page_id AND pg.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384'
  AND (ft.deleted_at IS NOT NULL OR pg.deleted_at IS NOT NULL);
DELETE FROM icid.segments sg
USING icid.pir_pages pg, icid.pirs pir, icid.properties p
WHERE sg.pir_page_id = pg.pir_page_id AND pg.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384'
  AND (sg.deleted_at IS NOT NULL OR pg.deleted_at IS NOT NULL);
DELETE FROM icid.flags f
USING icid.pirs pir, icid.properties p
WHERE f.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384' AND f.deleted_at IS NOT NULL;

-- Replaced pages (their title blocks first); a live flag still on one would stop the DELETE (foreign key)
DELETE FROM icid.pir_annotations a
USING icid.pir_pages pg, icid.pirs pir, icid.properties p
WHERE a.pir_page_id = pg.pir_page_id AND pg.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384'
  AND pg.deleted_at IS NOT NULL;
DELETE FROM icid.pir_pages pg
USING icid.pirs pir, icid.properties p
WHERE pg.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384' AND pg.deleted_at IS NOT NULL;

-- Soft-deleted PIRs (none per the audit) and soft-deleted properties (their bundles cascade)
DELETE FROM icid.pirs pir
USING icid.properties p
WHERE pir.property_id = p.id AND p.project_id = 'SE384' AND (pir.deleted_at IS NOT NULL OR p.deleted_at IS NOT NULL);
DELETE FROM icid.properties WHERE project_id = 'SE384' AND deleted_at IS NOT NULL;

-- HWS0023's empty task order "1"
DELETE FROM icid.task_orders WHERE project_id = 'HWS0023' AND task_order_number = '1';

-- 6. Move the live rows ---------------------------------------------------------------------------------------------
UPDATE icid.flags f
SET pay_item_id = target.contract_item_id, updated_at = now()
FROM icid.pirs pir, icid.properties p, icid.contract_items src, icid.contract_items target
WHERE f.pir_id = pir.id AND pir.property_id = p.id AND p.project_id = 'SE384'
  AND src.contract_item_id = f.pay_item_id
  AND target.project_id = 'HWS0023' AND target.item_no = src.item_no
  AND target.budget_code = CASE src.item_no WHEN '4.13 AAS' THEN 'HW-200(S)' WHEN '73.21AC' THEN 'HW-200(P)' END;

UPDATE icid.properties SET project_id = 'HWS0023', updated_at = now() WHERE project_id = 'SE384';
UPDATE icid.task_orders SET project_id = 'HWS0023', updated_at = now() WHERE project_id = 'SE384';

UPDATE icid.quantities q
SET project_id = 'HWS0023',
    budget_code = CASE q.pay_item_ref WHEN '4.13 AAS' THEN 'HW-200(S)' WHEN '73.21AC' THEN 'HW-200(P)' ELSE q.budget_code END
FROM icid.idrs i
WHERE q.idr_id = i.idr_id AND i.project_id = 'SE384' AND i.idr_number = '001';

UPDATE icid.idrs SET project_id = 'HWS0023', idr_number = 'Walk004', updated_at = now()
WHERE project_id = 'SE384' AND idr_number = '001';

-- 7. HWS0023's DOT rates, when it has none ----------------------------------------------------------------------------
UPDATE icid.projects h
SET cost_per_sf_4in = COALESCE(h.cost_per_sf_4in, s.cost_per_sf_4in),
    cost_per_sf_7in = COALESCE(h.cost_per_sf_7in, s.cost_per_sf_7in)
FROM icid.projects s
WHERE h.project_id = 'HWS0023' AND s.project_id = 'SE384';

-- 8. The end state --------------------------------------------------------------------------------------------------
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM icid.properties WHERE project_id = 'SE384')
     OR EXISTS (SELECT 1 FROM icid.task_orders WHERE project_id = 'SE384')
     OR EXISTS (SELECT 1 FROM icid.idrs WHERE project_id = 'SE384')
     OR EXISTS (SELECT 1 FROM icid.quantities WHERE project_id = 'SE384') THEN
    RAISE EXCEPTION 'M1: SE384 still has site, task-order, IDR or quantity rows';
  END IF;
  IF (SELECT count(*) FROM icid.properties WHERE project_id = 'HWS0023') <> 5
     OR (SELECT count(*) FROM icid.task_orders WHERE project_id = 'HWS0023') <> 2
     OR (SELECT count(*) FROM icid.contract_items WHERE project_id = 'HWS0023') <> 23
     OR (SELECT count(*) FROM icid.flags f JOIN icid.contract_items ci ON ci.contract_item_id = f.pay_item_id
         WHERE ci.project_id = 'HWS0023' AND f.deleted_at IS NULL AND f.claimed_by_idr_id IS NOT NULL) <> 15 THEN
    RAISE EXCEPTION 'M1: HWS0023 does not hold what it should (5 properties, 2 task orders, 23 items, 15 claimed flags)';
  END IF;
  IF EXISTS (SELECT 1 FROM icid.contract_items WHERE project_id = 'HWS0023' AND budget_code IN ('12345', '67890'))
     OR EXISTS (SELECT 1 FROM icid.quantities WHERE project_id = 'HWS0023' AND budget_code IN ('12345', '67890'))
     OR EXISTS (SELECT 1 FROM icid.idr_reports r JOIN icid.idrs i ON i.idr_id = r.idr_id
                WHERE i.project_id = 'HWS0023' AND r.report_data::text ~ '"budgetCode": "(12345|67890)"')
     OR EXISTS (SELECT 1 FROM icid.idr_field_edits e JOIN icid.idrs i ON i.idr_id = e.idr_id
                WHERE i.project_id = 'HWS0023'
                  AND (e.old_value->>'budgetCode' IN ('12345', '67890') OR e.new_value->>'budgetCode' IN ('12345', '67890')
                       OR (jsonb_typeof(e.old_value) = 'string' AND e.old_value #>> '{}' IN ('12345', '67890'))
                       OR (jsonb_typeof(e.new_value) = 'string' AND e.new_value #>> '{}' IN ('12345', '67890')))) THEN
    RAISE EXCEPTION 'M1: 12345 / 67890 is still somewhere in HWS0023';
  END IF;
  IF EXISTS (SELECT 1 FROM icid.flags f JOIN icid.pirs pir ON pir.id = f.pir_id JOIN icid.properties p ON p.id = pir.property_id
             JOIN icid.contract_items ci ON ci.contract_item_id = f.pay_item_id
             WHERE p.project_id = 'HWS0023' AND ci.project_id <> 'HWS0023') THEN
    RAISE EXCEPTION 'M1: a moved flag still names another project''s pay item';
  END IF;
END $$;

COMMIT;
