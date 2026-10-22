-- migrations/057_segment_reported_length.sql
--
-- Slice X.2: a curb segment can carry a reported length, as a flag carries a reported quantity. reported_length_ft
-- NULL means there is none and the drawn length_ft stands; set, it is what the segment bills (0 allowed), with an
-- optional length_delta_reason. The Construction Report's CC= keeps the drawn length.
--
-- Touches no live data: adds two nullable columns and a CHECK; modifies 0 rows. Run before deploying the Slice X.2
-- code. Safe to run twice.
--
-- Rollback (after rolling the code back):
--   ALTER TABLE icid.segments DROP CONSTRAINT chk_segments_reported_length,
--       DROP COLUMN length_delta_reason, DROP COLUMN reported_length_ft;

BEGIN;

ALTER TABLE icid.segments
    ADD COLUMN IF NOT EXISTS reported_length_ft NUMERIC,
    ADD COLUMN IF NOT EXISTS length_delta_reason TEXT;

ALTER TABLE icid.segments DROP CONSTRAINT IF EXISTS chk_segments_reported_length;
ALTER TABLE icid.segments
    ADD CONSTRAINT chk_segments_reported_length CHECK (reported_length_ft IS NULL OR reported_length_ft >= 0);

COMMIT;
