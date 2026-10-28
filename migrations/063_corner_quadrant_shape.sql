-- migrations/063_corner_quadrant_shape.sql
--
-- Slice Y5, the corner-quadrant flag shape. Widens two CHECKs to take 'corner_quadrant', keeping every value they had:
--   * flags.shape (chk_flags_shape): a flag may be a corner quadrant, its dimensions {"H", "C", "R1", "R2", "A", "B"}
--     (checked by the API, as every shape's are) and its computed_quantity the OE sheet's area.
--   * flag_measurements.outer_shape (chk_flag_measurements_outer_shape): a corner-quadrant flag's solo measurement
--     carries its shape, as every solo measurement does. A bundle is never one (the API refuses it).
-- Non-assessable areas and features keep their shapes (chk_nas_shape, chk_features_shape): they never take it.
--
-- Changes no row and no number. The code before Y5 runs unchanged after it; the Y5 code needs it (a corner-quadrant
-- flag would be refused by the old CHECKs). Run before deploying. Safe to run twice.
--
-- Rollback (after rolling the code back, with no corner-quadrant flag left): put back
--   chk_flags_shape CHECK (shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc')) and
--   chk_flag_measurements_outer_shape CHECK (outer_shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc',
--   'component_rectangles')).

BEGIN;

ALTER TABLE icid.flags DROP CONSTRAINT IF EXISTS chk_flags_shape;
ALTER TABLE icid.flags ADD CONSTRAINT chk_flags_shape
    CHECK (shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc', 'corner_quadrant'));

ALTER TABLE icid.flag_measurements DROP CONSTRAINT IF EXISTS chk_flag_measurements_outer_shape;
ALTER TABLE icid.flag_measurements ADD CONSTRAINT chk_flag_measurements_outer_shape
    CHECK (outer_shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc', 'component_rectangles',
                           'corner_quadrant'));

COMMIT;
