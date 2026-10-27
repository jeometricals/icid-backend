-- migrations/059_sidewalk_strip_tax_map_pdf.sql
--
-- Slice Y1.5: a page's sidewalk strip, a site's DOF tax-map PDF, and an edit history for a flag's assessability.
--   pir_annotations: sidewalk_strip_points (the strip of sidewalk in front of the lot, a closed polygon of 3 or more
--     {x, y} points in the page's native pixels; its first edge is the property line), _source (manual, or r1k
--     reserved), _set_at and _set_by. Points, source and time are all set or all NULL.
--   properties: the site's current DOF Digital Tax Map PDF (tax_map_pdf_path in bucket tax-maps, 059b), its source
--     (dof_map_library or upload), DOF's map id and effective date (a fetch's), when it was fetched or uploaded, and
--     who uploaded it (an upload's).
--   flag_edits: one row per change of a flag's surveyed_is_assessable or surveyed_non_assessable_reason, by the strip's
--     auto-flip (editor NULL) or a flag edit (the editor). Rows are only ever added.
--   The non-assessable reasons of flags and of non-assessable areas take outside_property_line.
--
-- Touches no live data: adds nullable columns, a table and CHECKs, and widens two CHECKs; modifies 0 rows. Run before
-- deploying the Slice Y1.5 code (and 059b, its bucket). Safe to run twice.
--
-- Rollback (after rolling the code back, and only while no row uses outside_property_line):
--   DROP TABLE icid.flag_edits;
--   ALTER TABLE icid.pir_annotations DROP CONSTRAINT chk_pir_annotations_sidewalk_strip,
--       DROP CONSTRAINT chk_pir_annotations_sidewalk_strip_points, DROP CONSTRAINT chk_pir_annotations_sidewalk_strip_source,
--       DROP COLUMN sidewalk_strip_set_by, DROP COLUMN sidewalk_strip_set_at, DROP COLUMN sidewalk_strip_source,
--       DROP COLUMN sidewalk_strip_points;
--   ALTER TABLE icid.properties DROP CONSTRAINT chk_properties_tax_map_pdf, DROP CONSTRAINT chk_properties_tax_map_pdf_source,
--       DROP COLUMN tax_map_pdf_uploaded_by, DROP COLUMN tax_map_pdf_fetched_at, DROP COLUMN tax_map_pdf_effective_date,
--       DROP COLUMN tax_map_pdf_map_id, DROP COLUMN tax_map_pdf_source, DROP COLUMN tax_map_pdf_path;
--   and put back the two reason CHECKs as 058 left them (schema.sql at 058).

BEGIN;

-- A page's sidewalk strip
ALTER TABLE icid.pir_annotations
    ADD COLUMN IF NOT EXISTS sidewalk_strip_points JSONB,
    ADD COLUMN IF NOT EXISTS sidewalk_strip_source TEXT,
    ADD COLUMN IF NOT EXISTS sidewalk_strip_set_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sidewalk_strip_set_by UUID REFERENCES icid.users(uuid);

ALTER TABLE icid.pir_annotations DROP CONSTRAINT IF EXISTS chk_pir_annotations_sidewalk_strip_points;
ALTER TABLE icid.pir_annotations DROP CONSTRAINT IF EXISTS chk_pir_annotations_sidewalk_strip_source;
ALTER TABLE icid.pir_annotations DROP CONSTRAINT IF EXISTS chk_pir_annotations_sidewalk_strip;
ALTER TABLE icid.pir_annotations
    ADD CONSTRAINT chk_pir_annotations_sidewalk_strip_points CHECK (sidewalk_strip_points IS NULL OR
        (jsonb_typeof(sidewalk_strip_points) = 'array' AND jsonb_array_length(sidewalk_strip_points) >= 3)),
    ADD CONSTRAINT chk_pir_annotations_sidewalk_strip_source
        CHECK (sidewalk_strip_source IS NULL OR sidewalk_strip_source IN ('manual', 'r1k')),
    ADD CONSTRAINT chk_pir_annotations_sidewalk_strip
        CHECK ((sidewalk_strip_points IS NULL) = (sidewalk_strip_source IS NULL)
               AND (sidewalk_strip_source IS NULL) = (sidewalk_strip_set_at IS NULL)
               AND (sidewalk_strip_set_by IS NULL OR sidewalk_strip_points IS NOT NULL));

-- A site's DOF tax-map PDF
ALTER TABLE icid.properties
    ADD COLUMN IF NOT EXISTS tax_map_pdf_path TEXT,
    ADD COLUMN IF NOT EXISTS tax_map_pdf_source TEXT,
    ADD COLUMN IF NOT EXISTS tax_map_pdf_map_id TEXT,
    ADD COLUMN IF NOT EXISTS tax_map_pdf_effective_date DATE,
    ADD COLUMN IF NOT EXISTS tax_map_pdf_fetched_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tax_map_pdf_uploaded_by UUID REFERENCES icid.users(uuid);

ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_tax_map_pdf_source;
ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_tax_map_pdf;
ALTER TABLE icid.properties
    ADD CONSTRAINT chk_properties_tax_map_pdf_source
        CHECK (tax_map_pdf_source IS NULL OR tax_map_pdf_source IN ('dof_map_library', 'upload')),
    ADD CONSTRAINT chk_properties_tax_map_pdf
        CHECK ((tax_map_pdf_path IS NULL) = (tax_map_pdf_source IS NULL)
               AND (tax_map_pdf_source IS NULL) = (tax_map_pdf_fetched_at IS NULL)
               AND (tax_map_pdf_source IS DISTINCT FROM 'dof_map_library'
                    OR (tax_map_pdf_map_id IS NOT NULL AND tax_map_pdf_uploaded_by IS NULL))
               AND (tax_map_pdf_source IS DISTINCT FROM 'upload'
                    OR (tax_map_pdf_uploaded_by IS NOT NULL AND tax_map_pdf_map_id IS NULL
                        AND tax_map_pdf_effective_date IS NULL))
               AND (tax_map_pdf_source IS NOT NULL
                    OR (tax_map_pdf_map_id IS NULL AND tax_map_pdf_effective_date IS NULL
                        AND tax_map_pdf_uploaded_by IS NULL)));

-- A flag's assessability, edited
CREATE TABLE IF NOT EXISTS icid.flag_edits (
    edit_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_id       UUID NOT NULL REFERENCES icid.flags(id),
    field_path    TEXT NOT NULL,
    old_value     JSONB NOT NULL,
    new_value     JSONB NOT NULL,
    reason        TEXT,
    editor_uuid   UUID REFERENCES icid.users(uuid),
    editor_stage  TEXT,
    edited_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_flag_edits_field
        CHECK (field_path IN ('surveyed_is_assessable', 'surveyed_non_assessable_reason')),
    CONSTRAINT chk_flag_edits_stage CHECK (editor_stage IS NULL OR editor_stage IN ('stage1', 'stage2')),
    CONSTRAINT chk_flag_edits_changed CHECK (old_value IS DISTINCT FROM new_value)
);
CREATE INDEX IF NOT EXISTS idx_flag_edits_flag ON icid.flag_edits (flag_id, edited_at DESC);

-- outside_property_line, for a flag or an area wholly outside its page's sidewalk strip
ALTER TABLE icid.flags DROP CONSTRAINT IF EXISTS chk_flags_non_assessable_reason;
ALTER TABLE icid.flags
    ADD CONSTRAINT chk_flags_non_assessable_reason CHECK (surveyed_non_assessable_reason IS NULL OR
        surveyed_non_assessable_reason IN ('tree_damage', 'city_tree', 'transition', 'no_driveway', 'hydrant',
                                           'fronting_city', 'utility', 'settlement', 'other',
                                           'tree', 'driveway', 'grass', 'dirt', 'existing_sidewalk', 'asphalt',
                                           'tree_pit', 'landmark', 'vault', 'subway', 'scaffolding',
                                           'other_perm_obstruction', 'other_temp_obstruction',
                                           'outside_property_line'));

ALTER TABLE icid.non_assessable_areas DROP CONSTRAINT IF EXISTS chk_nas_reason;
ALTER TABLE icid.non_assessable_areas
    ADD CONSTRAINT chk_nas_reason CHECK (reason IN ('driveway', 'grass', 'dirt', 'existing_sidewalk', 'asphalt',
                                                    'tree_pit', 'landmark', 'other', 'outside_property_line'));

COMMIT;
