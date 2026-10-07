-- migrations/023_truck_add.sql
-- Reviewer-added trucks (Slice K3-fix2, 2026-10-07): a reviewer can add a truck to a Concrete Truck & Mix Info
-- report, and every truck gains an id.
--
--   icid.idr_field_edits
--     ~ chk_idr_field_edits_type        now allows 'field_change', 'pay_item_revision', 'pay_item_add',
--                                       'pay_item_approve', 'truck_add' (was the first four)
--     ~ chk_idr_field_edits_old_value   old_value is NULL for 'pay_item_add' and 'truck_add', and for nothing else
--                                       (was: for 'pay_item_add' only)
--
--   icid.idr_reports.report_data        -- every trucks entry gains an "id" (a uuid, as text)
--
-- An added truck is one more row of the edit log:
--   edit_type   'truck_add'
--   field_path  trucks[<truck id>]         (the truck itself, like 'pay_item_add' and payItems[<item id>])
--   old_value   NULL                       (there was nothing)
--   new_value   the whole truck, with its id
--   editor_uuid, editor_stage, edited_at   who added it, at which stage, when
--
-- Trucks are entries in report_data, not rows of a table, and had nothing to tell one from another but their
-- position. The export has to find the truck a reviewer added (it prints that row in blue), so the UPDATE below
-- gives every existing truck an id; from here on, submitting an IDR gives one to any truck still without it, as it
-- does for pay items (migration 020). Nothing else about a truck changes, and a report's updated_at is left alone.
-- A truck's own fields are still named by position (trucks[0].slump): only the added row is found by its id.
--
-- No existing edit changes. Idempotent: the CHECKs are dropped IF EXISTS and re-created, and the UPDATE only
-- touches reports that still hold a truck without an id. Run in the Supabase SQL editor, after 022, and before the
-- backend that adds trucks is deployed (its INSERT fails the old CHECK). Everything between BEGIN and COMMIT is
-- one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The CHECKs are the ones from migrations 021 and 020:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idr_field_edits'::regclass AND conname IN ('chk_idr_field_edits_type', 'chk_idr_field_edits_old_value')
--   ORDER BY conname;
--   Expected: chk_idr_field_edits_old_value  CHECK (((old_value IS NULL) = (edit_type = 'pay_item_add'::text)))
--             chk_idr_field_edits_type       CHECK ((edit_type = ANY (ARRAY['field_change'::text, 'pay_item_revision'::text, 'pay_item_add'::text, 'pay_item_approve'::text])))

-- 2. The edits there are, by type and by whether they hold an old value (to compare afterwards):
--   SELECT edit_type, old_value IS NULL AS no_old_value, count(*) FROM icid.idr_field_edits
--   GROUP BY edit_type, old_value IS NULL ORDER BY edit_type;
--   Expected: no_old_value is true for 'pay_item_add' only

-- 3. How many trucks there are, and how many already have an id:
--   SELECT count(*) AS trucks, count(*) FILTER (WHERE jsonb_typeof(e) = 'object' AND e ? 'id') AS with_id,
--          count(*) FILTER (WHERE jsonb_typeof(e) <> 'object') AS not_objects
--   FROM icid.idr_reports r, jsonb_array_elements(r.report_data->'trucks') e
--   WHERE jsonb_typeof(r.report_data->'trucks') = 'array';
--   Expected: with_id = 0 on a first run (note trucks and not_objects for the check afterwards)

-- 4. A fingerprint of every truck without its id, to compare afterwards:
--   SELECT md5(string_agg((CASE WHEN jsonb_typeof(e) = 'object' THEN e - 'id' ELSE e END)::text, '|' ORDER BY r.report_id, ord)) AS fingerprint
--   FROM icid.idr_reports r, jsonb_array_elements(r.report_data->'trucks') WITH ORDINALITY AS t(e, ord)
--   WHERE jsonb_typeof(r.report_data->'trucks') = 'array';

-- 5. A fingerprint of everything else in the reports that hold trucks, to compare afterwards:
--   SELECT md5(string_agg((r.report_data - 'trucks')::text || r.updated_at::text, '|' ORDER BY r.report_id)) AS fingerprint
--   FROM icid.idr_reports r
--   WHERE jsonb_typeof(r.report_data->'trucks') = 'array';

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.idr_field_edits DROP CONSTRAINT IF EXISTS chk_idr_field_edits_type;

ALTER TABLE icid.idr_field_edits ADD CONSTRAINT chk_idr_field_edits_type
    CHECK (edit_type IN ('field_change', 'pay_item_revision', 'pay_item_add', 'pay_item_approve', 'truck_add'));

ALTER TABLE icid.idr_field_edits DROP CONSTRAINT IF EXISTS chk_idr_field_edits_old_value;

ALTER TABLE icid.idr_field_edits ADD CONSTRAINT chk_idr_field_edits_old_value
    CHECK ((old_value IS NULL) = (edit_type IN ('pay_item_add', 'truck_add')));

UPDATE icid.idr_reports r
SET report_data = jsonb_set(r.report_data, '{trucks}', (
        SELECT jsonb_agg(
            CASE WHEN jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id')
                 THEN e.item || jsonb_build_object('id', gen_random_uuid()::text)
                 ELSE e.item END
            ORDER BY e.ord)
        FROM jsonb_array_elements(r.report_data->'trucks') WITH ORDINALITY AS e(item, ord)
    ))
WHERE jsonb_typeof(r.report_data->'trucks') = 'array'
  AND EXISTS (
      SELECT 1 FROM jsonb_array_elements(r.report_data->'trucks') AS e(item)
      WHERE jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id')
  );

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The two CHECKs as they now stand, and the third untouched:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idr_field_edits'::regclass AND contype = 'c' ORDER BY conname;
--   Expected: chk_idr_field_edits_old_value  ... (old_value IS NULL) = (edit_type = ANY (ARRAY['pay_item_add'::text, 'truck_add'::text]))
--             chk_idr_field_edits_stage      (as before)
--             chk_idr_field_edits_type       ... ARRAY['field_change'::text, 'pay_item_revision'::text, 'pay_item_add'::text, 'pay_item_approve'::text, 'truck_add'::text]

-- B. No edit changed:
--   (run pre-check 2 again)
--   Expected: the same rows and counts

-- C. Every truck has an id, and no two share one:
--   SELECT count(*) AS trucks, count(*) FILTER (WHERE jsonb_typeof(e) = 'object' AND e ? 'id') AS with_id,
--          count(DISTINCT e->>'id') AS distinct_ids, count(*) FILTER (WHERE jsonb_typeof(e) <> 'object') AS not_objects
--   FROM icid.idr_reports r, jsonb_array_elements(r.report_data->'trucks') e
--   WHERE jsonb_typeof(r.report_data->'trucks') = 'array';
--   Expected: trucks and not_objects as in pre-check 3; with_id = distinct_ids = trucks - not_objects

-- D. Nothing else about any truck changed, and none moved:
--   (run pre-check 4 again)
--   Expected: the same fingerprint

-- E. Nothing else in those reports changed, their updated_at included:
--   (run pre-check 5 again)
--   Expected: the same fingerprint
