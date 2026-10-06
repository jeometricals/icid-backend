-- migrations/021_pay_item_approve.sql
-- Pay-item approvals (Slice K2.5, 2026-10-06): a reviewer can approve a pay item as it stands.
--
--   icid.idr_field_edits
--     ~ chk_idr_field_edits_type   now allows 'field_change', 'pay_item_revision', 'pay_item_add',
--                                  'pay_item_approve' (was the first three)
--
-- An approval is one more row of the edit log:
--   edit_type   'pay_item_approve'
--   field_path  payItems[<item id>]        (the item itself, like 'pay_item_add')
--   old_value   the quantity approved
--   new_value   the same quantity          (an approval changes nothing in the report)
--   editor_uuid, editor_stage, edited_at   who approved it, at which stage, when
--
-- chk_idr_field_edits_old_value is left as it is: only 'pay_item_add' has no old_value, and an
-- approval has one.
--
-- From this slice on, a stage can only be approved once its reviewer has approved, revised or
-- added every pay item at that stage. That rule lives in the API; nothing here enforces it.
--
-- No existing row changes. Idempotent: the CHECK is dropped IF EXISTS and re-created. Run in
-- the Supabase SQL editor, after 020. Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The CHECK is the one from migration 020:
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idr_field_edits'::regclass AND conname = 'chk_idr_field_edits_type';
--   Expected: CHECK ((edit_type = ANY (ARRAY['field_change'::text, 'pay_item_revision'::text, 'pay_item_add'::text])))

-- 2. The edits there are, by type (to compare afterwards):
--   SELECT edit_type, count(*) FROM icid.idr_field_edits GROUP BY edit_type ORDER BY edit_type;

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.idr_field_edits DROP CONSTRAINT IF EXISTS chk_idr_field_edits_type;

ALTER TABLE icid.idr_field_edits ADD CONSTRAINT chk_idr_field_edits_type
    CHECK (edit_type IN ('field_change', 'pay_item_revision', 'pay_item_add', 'pay_item_approve'));

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The CHECK now lists four types:
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.idr_field_edits'::regclass AND conname = 'chk_idr_field_edits_type';
--   Expected: ... ARRAY['field_change'::text, 'pay_item_revision'::text, 'pay_item_add'::text, 'pay_item_approve'::text]

-- B. The other two CHECKs are untouched:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'icid.idr_field_edits'::regclass AND contype = 'c' ORDER BY conname;
--   Expected: chk_idr_field_edits_old_value, chk_idr_field_edits_stage, chk_idr_field_edits_type

-- C. No edit changed:
--   SELECT edit_type, count(*) FROM icid.idr_field_edits GROUP BY edit_type ORDER BY edit_type;
--   Expected: the same counts as pre-check 2
