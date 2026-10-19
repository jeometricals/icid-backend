-- migrations/043_property_community_board.sql
--
-- Slice A: a property's Community Board (CB), as DOT's sheets list it beside the block and lot. Free text, at most 10
-- characters, optional for both kinds. No backfill: every existing property gets NULL, filled in through the UI.
--
-- The code that reads properties selects the column, so run this before deploying it.
--
-- Rollback: ALTER TABLE icid.properties DROP COLUMN community_board; (after rolling the code back).
-- Safe to run twice.

BEGIN;

ALTER TABLE icid.properties ADD COLUMN IF NOT EXISTS community_board TEXT;

ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_community_board_length;
ALTER TABLE icid.properties ADD CONSTRAINT chk_properties_community_board_length
    CHECK (community_board IS NULL OR char_length(community_board) <= 10);

COMMIT;
