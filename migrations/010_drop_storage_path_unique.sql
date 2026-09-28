-- migrations/010_drop_storage_path_unique.sql
-- Corrects an oversight in migration 009 where the DROP CONSTRAINT was intended
-- but silently failed. This migration explicitly drops the unique constraint on
-- storage_path so future paths can be reused if needed.
--
--   Drops report_attachments_storage_path_key (the UNIQUE on
--   icid.report_attachments.storage_path, created by 008).
--
-- Run in the Supabase SQL editor. Everything between BEGIN and COMMIT is one
-- transaction: if any statement fails, nothing is applied.

BEGIN;

ALTER TABLE icid.report_attachments DROP CONSTRAINT IF EXISTS report_attachments_storage_path_key;

COMMIT;
