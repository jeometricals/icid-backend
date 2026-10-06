-- migrations/020_field_edits.sql
-- Reviewer edits (Slice K0, 2026-10-06): the log of what a reviewer changed on an IDR, and an id on every pay item.
--
--   icid.idr_field_edits                       -- one row per edit; rows are only ever added
--     edit_id       UUID PK, gen_random_uuid()
--     idr_id        UUID NOT NULL              -- FK -> idrs.idr_id, ON DELETE CASCADE
--     report_id     UUID                       -- FK -> idr_reports.report_id, ON DELETE CASCADE; NULL for a header field
--     field_path    TEXT NOT NULL              -- which field: see below
--     edit_type     TEXT NOT NULL              -- 'field_change', 'pay_item_revision' or 'pay_item_add'
--     old_value     JSONB                      -- what was there; NULL (no value at all) only for 'pay_item_add'
--     new_value     JSONB NOT NULL             -- what the reviewer put
--     editor_uuid   UUID NOT NULL              -- FK -> users.uuid
--     editor_stage  TEXT NOT NULL              -- 'stage1' or 'stage2': the stage the IDR was in
--     edited_at     TIMESTAMPTZ NOT NULL DEFAULT now()
--     + idx_idr_field_edits_idr    (idr_id, edited_at)
--     + idx_idr_field_edits_field  (report_id, field_path)
--
--   icid.idr_reports.report_data               -- every payItems entry gains an "id" (a uuid, as text)
--
-- An edit is applied and logged: the statement that records it also writes the new value into
-- the IDR (a header column, or the report's report_data), so the IDR always holds the current
-- value and every reader that knows nothing of edits stays right. The value before the first
-- edit of a field is that row's old_value; a field's edits, oldest first, are its history.
--
-- field_path, with the keys as report_data stores them:
--   header.<column>                  a header field (report_id NULL), e.g. header.work_start_time
--   <key>.<key>...                   a report field, e.g. description, workforce.foremen,
--                                    safetyChecks.plasticBarrels, equipment.backhoe.model
--   <list>[<n>].<key>                a row of a list, by position from 0, e.g. additionalWorkforce[0].count
--   payItems[<item id>].<key>        a pay item's field, by the item's id; payQuantity is a 'pay_item_revision'
--   payItems[<item id>]              a pay item a reviewer added ('pay_item_add'; new_value is the whole item)
--
-- Pay items are entries in report_data, not rows of a table, and had nothing to tell one from
-- another but their position. The UPDATE below gives every existing one an id; from here on,
-- submitting an IDR gives one to any item still without it. Nothing else about an item changes,
-- and a report's updated_at is left alone.
--
-- ON DELETE CASCADE on both keys: demo users' IDRs are hard-deleted, and a returned draft's
-- inspector can delete a report; the edits go with what they were made on.
--
-- Idempotent: the table and indexes are created IF NOT EXISTS, and the UPDATE only touches
-- reports that still hold a pay item without an id. Run in the Supabase SQL editor, after 019.
-- Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The table does not exist yet:
--   SELECT to_regclass('icid.idr_field_edits');
--   Expected: NULL

-- 2. How many pay items there are, and how many already have an id:
--   SELECT count(*) AS pay_items, count(*) FILTER (WHERE e ? 'id') AS with_id
--   FROM icid.idr_reports r, jsonb_array_elements(r.report_data->'payItems') e
--   WHERE jsonb_typeof(r.report_data->'payItems') = 'array';
--   Expected: with_id = 0 (note pay_items for the check afterwards)

-- 3. A fingerprint of every pay item without its id, to compare afterwards:
--   SELECT md5(string_agg((e - 'id')::text, '|' ORDER BY r.report_id, ord)) AS fingerprint
--   FROM icid.idr_reports r, jsonb_array_elements(r.report_data->'payItems') WITH ORDINALITY AS t(e, ord)
--   WHERE jsonb_typeof(r.report_data->'payItems') = 'array';

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS icid.idr_field_edits (
    edit_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idr_id        UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    report_id     UUID REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE,
    field_path    TEXT NOT NULL,
    edit_type     TEXT NOT NULL,
    old_value     JSONB,
    new_value     JSONB NOT NULL,
    editor_uuid   UUID NOT NULL REFERENCES icid.users(uuid),
    editor_stage  TEXT NOT NULL,
    edited_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_idr_field_edits_type CHECK (edit_type IN ('field_change', 'pay_item_revision', 'pay_item_add')),
    CONSTRAINT chk_idr_field_edits_stage CHECK (editor_stage IN ('stage1', 'stage2')),
    CONSTRAINT chk_idr_field_edits_old_value CHECK ((old_value IS NULL) = (edit_type = 'pay_item_add'))
);

CREATE INDEX IF NOT EXISTS idx_idr_field_edits_idr ON icid.idr_field_edits(idr_id, edited_at);

CREATE INDEX IF NOT EXISTS idx_idr_field_edits_field ON icid.idr_field_edits(report_id, field_path);

UPDATE icid.idr_reports r
SET report_data = jsonb_set(r.report_data, '{payItems}', (
        SELECT jsonb_agg(
            CASE WHEN jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id')
                 THEN e.item || jsonb_build_object('id', gen_random_uuid()::text)
                 ELSE e.item END
            ORDER BY e.ord)
        FROM jsonb_array_elements(r.report_data->'payItems') WITH ORDINALITY AS e(item, ord)
    ))
WHERE jsonb_typeof(r.report_data->'payItems') = 'array'
  AND EXISTS (
      SELECT 1 FROM jsonb_array_elements(r.report_data->'payItems') AS e(item)
      WHERE jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id')
  );

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The table's columns and constraints:
--   SELECT column_name, data_type, is_nullable FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'idr_field_edits' ORDER BY ordinal_position;
--   Expected: 10 rows; report_id and old_value are the only nullable ones
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idr_field_edits'::regclass ORDER BY conname;
--   Expected: the three CHECKs, the primary key, and FKs to idrs, idr_reports (both ON DELETE CASCADE) and users

-- B. Every pay item has an id, and no two share one:
--   SELECT count(*) AS pay_items, count(*) FILTER (WHERE e ? 'id') AS with_id, count(DISTINCT e->>'id') AS distinct_ids
--   FROM icid.idr_reports r, jsonb_array_elements(r.report_data->'payItems') e
--   WHERE jsonb_typeof(r.report_data->'payItems') = 'array';
--   Expected: all three equal, and pay_items as in pre-check 2

-- C. Nothing else about any pay item changed, and none moved:
--   (run pre-check 3 again)
--   Expected: the same fingerprint
