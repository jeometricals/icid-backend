-- migrations/061_flag_measurements.sql
--
-- Slice Y3a, the measurement foundation. Changes no number.
--   * icid.flag_measurements: what a flag's area is measured from. In Y3a every flag has its own, a solo measurement:
--     its outer shape and dimensions are the flag's, outer_area its computed_quantity, deductions [] (a flag's
--     non-assessable areas and subtracted features stay on the flag until Y3b) and needs_remeasure false. The flag's
--     pay item, thickness and assessment stay on the flag only (Y3b adds them when measurements bundle flags).
--   * icid.measurement_edits: shaped as flag_edits (Y1.5), on a measurement. Created empty; Y3b's remesh is its first
--     writer.
--   * flags.measurement_id (its measurement), flags.area_share_fraction (the part of the measurement it takes, 1.0000
--     for a solo one) and flags.pir_flag_no (the signed PIR's own flag number, set only by the Smart PIR import; unique
--     per page among live flags).
--   * Backfill: one solo measurement for every flag, live and soft-deleted (a claimed flag still counts once deleted),
--     across every project. Checked live on 2026-10-27: prod 354 flags (207 live, 147 soft-deleted), dev 5 live. Every
--     computed_quantity has 2 decimal places, so outer_area NUMERIC(10,2) holds each exactly; the statement refuses
--     to run otherwise.
--
-- Three foreign keys to users (flag_measurements.created_by and .updated_by, measurement_edits.editor_uuid;
-- count_user_references counts them).
--
-- Run before deploying the Slice Y3a code, then 061b right after the deploy (it backfills any flag the code before
-- Y3a added in between and makes flags.measurement_id NOT NULL). The code before Y3a runs unchanged after this file.
-- Safe to run twice.
--
-- Step 1, before running: the counts this file backfills (run on its own, read-only).
--   SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS live, count(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted,
--          count(*) AS flags_to_backfill FROM icid.flags WHERE measurement_id IS NULL;
--   (Before 061 the column doesn't exist: drop the WHERE.)
--
-- Rollback (after rolling the code back): ALTER TABLE icid.flags DROP COLUMN measurement_id, DROP COLUMN
-- area_share_fraction, DROP COLUMN pir_flag_no; DROP TABLE icid.measurement_edits; DROP TABLE icid.flag_measurements;

BEGIN;

CREATE TABLE IF NOT EXISTS icid.flag_measurements (
    measurement_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_id            UUID NOT NULL REFERENCES icid.pirs(id),
    outer_shape       TEXT NOT NULL,
    outer_dimensions  JSONB NOT NULL,
    outer_area        NUMERIC(10,2) NOT NULL,
    deductions        JSONB NOT NULL DEFAULT '[]'::jsonb,
    needs_remeasure   BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        UUID NOT NULL REFERENCES icid.users(uuid),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_flag_measurements_outer_shape
        CHECK (outer_shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc')),
    CONSTRAINT chk_flag_measurements_outer_area CHECK (outer_area >= 0),
    CONSTRAINT chk_flag_measurements_deductions CHECK (jsonb_typeof(deductions) = 'array')
);
CREATE INDEX IF NOT EXISTS idx_flag_measurements_pir ON icid.flag_measurements (pir_id);

CREATE TABLE IF NOT EXISTS icid.measurement_edits (
    edit_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    measurement_id  UUID NOT NULL REFERENCES icid.flag_measurements(measurement_id),
    field_path      TEXT NOT NULL,
    old_value       JSONB NOT NULL,
    new_value       JSONB NOT NULL,
    reason          TEXT,
    editor_uuid     UUID REFERENCES icid.users(uuid),
    editor_stage    TEXT,
    edited_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_measurement_edits_stage CHECK (editor_stage IS NULL OR editor_stage IN ('stage1', 'stage2')),
    CONSTRAINT chk_measurement_edits_changed CHECK (old_value IS DISTINCT FROM new_value)
);
CREATE INDEX IF NOT EXISTS idx_measurement_edits_measurement ON icid.measurement_edits (measurement_id, edited_at DESC);

ALTER TABLE icid.flags
    ADD COLUMN IF NOT EXISTS measurement_id UUID REFERENCES icid.flag_measurements(measurement_id),
    ADD COLUMN IF NOT EXISTS area_share_fraction NUMERIC(5,4) NOT NULL DEFAULT 1.0000,
    ADD COLUMN IF NOT EXISTS pir_flag_no TEXT;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_flags_area_share_fraction') THEN
        ALTER TABLE icid.flags ADD CONSTRAINT chk_flags_area_share_fraction
            CHECK (area_share_fraction > 0 AND area_share_fraction <= 1);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_flags_pir_flag_no') THEN
        ALTER TABLE icid.flags ADD CONSTRAINT chk_flags_pir_flag_no
            CHECK (pir_flag_no IS NULL OR (pir_flag_no = btrim(pir_flag_no) AND length(pir_flag_no) BETWEEN 1 AND 16));
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_flags_measurement ON icid.flags (measurement_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_flags_page_pir_flag_no
    ON icid.flags (pir_page_id, pir_flag_no) WHERE pir_flag_no IS NOT NULL AND deleted_at IS NULL;

-- Backfill: a solo measurement per flag without one, live or soft-deleted
DO $$
DECLARE
    live_count INT;
    deleted_count INT;
BEGIN
    IF EXISTS (SELECT 1 FROM icid.flags WHERE measurement_id IS NULL
               AND (scale(computed_quantity) > 2 OR computed_quantity >= 1e8 OR computed_quantity < 0)) THEN
        RAISE EXCEPTION 'A flag''s computed_quantity would not fit outer_area NUMERIC(10,2) exactly: nothing changed';
    END IF;
    SELECT count(*) FILTER (WHERE deleted_at IS NULL), count(*) FILTER (WHERE deleted_at IS NOT NULL)
    INTO live_count, deleted_count FROM icid.flags WHERE measurement_id IS NULL;
    RAISE NOTICE 'Backfilling % flags: % live, % soft-deleted', live_count + deleted_count, live_count, deleted_count;
END $$;

WITH pairs AS MATERIALIZED (
    SELECT f.id AS flag_id, gen_random_uuid() AS measurement_id FROM icid.flags f WHERE f.measurement_id IS NULL
),
measurements AS (
    INSERT INTO icid.flag_measurements (measurement_id, pir_id, outer_shape, outer_dimensions, outer_area, created_at,
                                        created_by, updated_at)
    SELECT pairs.measurement_id, f.pir_id, f.shape, f.dimensions, f.computed_quantity, f.created_at, f.created_by,
           f.updated_at
    FROM pairs JOIN icid.flags f ON f.id = pairs.flag_id
    RETURNING measurement_id
)
UPDATE icid.flags f
SET measurement_id = pairs.measurement_id
FROM pairs
WHERE f.id = pairs.flag_id AND pairs.measurement_id IN (SELECT measurement_id FROM measurements);

-- What the backfill must leave: every flag measured, by a measurement of its own, of exactly its area
DO $$
DECLARE
    flag_count INT;
    measurement_count INT;
BEGIN
    IF EXISTS (SELECT 1 FROM icid.flags WHERE measurement_id IS NULL) THEN
        RAISE EXCEPTION 'A flag has no measurement after the backfill';
    END IF;
    IF EXISTS (SELECT measurement_id FROM icid.flags GROUP BY measurement_id HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'A measurement has more than one flag';
    END IF;
    IF EXISTS (SELECT 1 FROM icid.flags f JOIN icid.flag_measurements m ON m.measurement_id = f.measurement_id
               WHERE m.outer_area IS DISTINCT FROM f.computed_quantity OR m.outer_shape IS DISTINCT FROM f.shape
                  OR m.outer_dimensions IS DISTINCT FROM f.dimensions OR m.pir_id IS DISTINCT FROM f.pir_id
                  OR f.area_share_fraction <> 1) THEN
        RAISE EXCEPTION 'A measurement does not match its flag';
    END IF;
    SELECT count(*) INTO flag_count FROM icid.flags;
    SELECT count(*) INTO measurement_count FROM icid.flag_measurements;
    RAISE NOTICE 'Done: % flags, % measurements', flag_count, measurement_count;
END $$;

COMMIT;
