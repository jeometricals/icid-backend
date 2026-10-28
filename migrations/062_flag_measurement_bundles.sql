-- migrations/062_flag_measurement_bundles.sql
--
-- Slice Y3b-1, measurement bundles' columns. Additive, no backfill: changes no row and no number.
--   * flag_measurements.pay_item_id (-> contract_items, ON DELETE SET NULL, as flags.pay_item_id), surveyed_thickness
--     and surveyed_is_assessable: a bundle's classification, its members' own. NULL on a solo measurement, which reads
--     its flag's; set when a bundle forms (Y3b-2's bundle route). A measurement is a bundle exactly when
--     surveyed_is_assessable is set: the thickness comes with it, and a solo one has no pay item of its own.
--   * flag_measurements.retired_at: when a bundle was emptied (its last member left). Rows are never deleted: their
--     measurement_edits rows name them.
--   * outer_shape takes component_rectangles, outer_dimensions then {"rectangles": [{"L": n, "W": n}, ...]}, 1 to 3
--     rectangles with lengths above 0.
--
-- Every existing measurement is solo (Y3a), so every column added here is NULL on every row. The Y3b-1 code needs this
-- file (its readers read the columns); the code before it runs unchanged after it. Run before deploying. Safe to run
-- twice.
--
-- Rollback (after rolling the code back): ALTER TABLE icid.flag_measurements DROP CONSTRAINT
-- chk_flag_measurements_component_rectangles, DROP CONSTRAINT chk_flag_measurements_classification, DROP COLUMN
-- pay_item_id, DROP COLUMN surveyed_thickness, DROP COLUMN surveyed_is_assessable, DROP COLUMN retired_at; and put
-- 061's chk_flag_measurements_outer_shape back.

BEGIN;

ALTER TABLE icid.flag_measurements
    ADD COLUMN IF NOT EXISTS pay_item_id UUID REFERENCES icid.contract_items(contract_item_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS surveyed_thickness TEXT,
    ADD COLUMN IF NOT EXISTS surveyed_is_assessable BOOLEAN,
    ADD COLUMN IF NOT EXISTS retired_at TIMESTAMPTZ;

ALTER TABLE icid.flag_measurements DROP CONSTRAINT IF EXISTS chk_flag_measurements_outer_shape;
ALTER TABLE icid.flag_measurements ADD CONSTRAINT chk_flag_measurements_outer_shape
    CHECK (outer_shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc', 'component_rectangles'));

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_flag_measurements_component_rectangles') THEN
        ALTER TABLE icid.flag_measurements ADD CONSTRAINT chk_flag_measurements_component_rectangles
            CHECK (outer_shape <> 'component_rectangles' OR (
                outer_dimensions - 'rectangles' = '{}'::jsonb
                AND jsonb_typeof(outer_dimensions -> 'rectangles') = 'array'
                AND jsonb_array_length(outer_dimensions -> 'rectangles') BETWEEN 1 AND 3
                AND NOT jsonb_path_exists(outer_dimensions,
                    '$.rectangles[*] ? (!(@.type() == "object" && @.L.type() == "number" && @.W.type() == "number"
                                          && @.L > 0 && @.W > 0))')
            ));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_flag_measurements_classification') THEN
        ALTER TABLE icid.flag_measurements ADD CONSTRAINT chk_flag_measurements_classification
            CHECK ((surveyed_thickness IS NULL) = (surveyed_is_assessable IS NULL)
                   AND (surveyed_thickness IS NULL OR surveyed_thickness IN ('4in', '7in', '7in_as_4in'))
                   AND (surveyed_is_assessable IS NOT NULL OR pay_item_id IS NULL));
    END IF;
END $$;

DO $$
DECLARE
    total INT;
    bundled INT;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE surveyed_is_assessable IS NOT NULL OR retired_at IS NOT NULL)
    INTO total, bundled FROM icid.flag_measurements;
    IF bundled > 0 THEN
        RAISE EXCEPTION 'Expected every measurement solo and live, found % bundled or retired', bundled;
    END IF;
    RAISE NOTICE 'Done: % measurements, every one solo', total;
END $$;

COMMIT;
