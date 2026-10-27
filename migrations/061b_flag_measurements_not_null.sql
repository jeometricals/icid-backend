-- migrations/061b_flag_measurements_not_null.sql
--
-- Slice Y3a, after its code is deployed: every flag has a measurement from now on. A flag the code before Y3a added
-- between 061 and the deploy has none: it gets its solo measurement here, as 061 gave every other flag one, and then
-- flags.measurement_id is made NOT NULL. Prints how many it backfilled (0 when nothing was added in between).
--
-- Run after 061 and the Slice Y3a deploy. Safe to run twice.
--
-- Rollback: ALTER TABLE icid.flags ALTER COLUMN measurement_id DROP NOT NULL;

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM icid.flags WHERE measurement_id IS NULL
               AND (scale(computed_quantity) > 2 OR computed_quantity >= 1e8 OR computed_quantity < 0)) THEN
        RAISE EXCEPTION 'A flag''s computed_quantity would not fit outer_area NUMERIC(10,2) exactly: nothing changed';
    END IF;
    RAISE NOTICE 'Backfilling % flags added since 061', (SELECT count(*) FROM icid.flags WHERE measurement_id IS NULL);
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

ALTER TABLE icid.flags ALTER COLUMN measurement_id SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (SELECT measurement_id FROM icid.flags GROUP BY measurement_id HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'A measurement has more than one flag';
    END IF;
    IF EXISTS (SELECT 1 FROM icid.flags f JOIN icid.flag_measurements m ON m.measurement_id = f.measurement_id
               WHERE m.outer_area IS DISTINCT FROM f.computed_quantity OR m.outer_shape IS DISTINCT FROM f.shape
                  OR m.outer_dimensions IS DISTINCT FROM f.dimensions OR m.pir_id IS DISTINCT FROM f.pir_id
                  OR f.area_share_fraction <> 1) THEN
        RAISE EXCEPTION 'A measurement does not match its flag';
    END IF;
    RAISE NOTICE 'Done: % flags, every one measured', (SELECT count(*) FROM icid.flags);
END $$;

COMMIT;
