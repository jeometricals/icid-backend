-- migrations/034_smart_pir_data_model.sql
-- Smart PIR data model (2026-10-15): what a digitized PIR page carries beyond its flags, so a whole sketch can be
-- imported in one call.
--
--   icid.flags                 (changed)
--     + defect_codes   TEXT[] NOT NULL    DOT defect codes, a subset of 1 2 3 4 5 6A 6B 6C; default {}
--     chk_flags_non_assessable_reason      widened: the 9 values it had plus the R1e.1 survey's (22 in all)
--
--   icid.non_assessable_areas  (new)       pieces of a flag that are not assessed (a driveway, grass, ...)
--     non_assessable_area_id UUID PK
--     flag_id          UUID NOT NULL      FK -> flags.id (no action: flags are never hard-deleted)
--     display_order    INT NOT NULL       >= 1; unique per flag among live areas (nas_flag_order_live_uq)
--     shape            TEXT NOT NULL      rectangle | trapezoid | triangle | polygon | arc
--     dimensions       JSONB NOT NULL     checked per shape by the API, as a flag's are
--     sketch_x, sketch_y REAL NOT NULL    native page pixels, as a flag's; rotation REAL, default 0
--     reason           TEXT NOT NULL      driveway | grass | dirt | existing_sidewalk | asphalt | tree_pit |
--                                         landmark | other
--     computed_quantity NUMERIC NOT NULL  the shape's area (sq ft), computed by the server; taken off the flag's
--     created_at, updated_at, created_by (FK -> users), deleted_at, deleted_by (FK -> users ON DELETE SET NULL)
--
--   icid.features              (new)       point obstructions and sized features drawn on a page
--     feature_id       UUID PK
--     pir_page_id      UUID NOT NULL      FK -> pir_pages (no action: pages are never hard-deleted)
--     flag_id          UUID NULL          FK -> flags.id: the flag a 'subtract' feature is taken off (required then)
--     feature_type     TEXT NOT NULL      manhole | grate | ... | other
--     area_impact      TEXT NOT NULL      marker_only (default) | subtract | sits_on_top; the last two need a shape
--     sketch_x, sketch_y REAL NOT NULL; rotation REAL NOT NULL default 0
--     shape            TEXT NULL          the flag shapes plus circle; set together with dimensions
--     dimensions       JSONB NULL
--     label            TEXT NULL
--     pay_item_id      UUID NULL          FK -> contract_items ON DELETE SET NULL
--     computed_quantity NUMERIC NULL      its area (sq ft) when it has a shape; reported_quantity NUMERIC NULL > 0
--     created_at, updated_at, created_by, deleted_at, deleted_by
--
--   icid.segments              (new)       curb runs drawn on a page
--     segment_id       UUID PK
--     pir_page_id      UUID NOT NULL      FK -> pir_pages (no action)
--     segment_type     TEXT NOT NULL      curb_concrete | curb_concrete_depressed | curb_steel_faced |
--                                         curb_steel_faced_corner | curb_granite | other
--     points           JSONB NOT NULL     [{x, y}, ...] in native page pixels, two or more (checked by the API)
--     length_ft        NUMERIC NULL       from the points and the page's scale at write time; NULL without a scale
--     pay_item_id      UUID NULL          FK -> contract_items ON DELETE SET NULL
--     note             TEXT NULL
--     created_at, updated_at, created_by, deleted_at, deleted_by
--
--   icid.pir_annotations       (new)       a page's title block, one row per page (PK pir_page_id, no action)
--     contract_no, violation_no, boro (the five boroughs), property_address, block, lot, cross_street_left,
--     cross_street_right, frontage_street, tax_map_width_ft, cc_curb_to_curb_ft, property_status,
--     corner_property_status, violation_not_removed_due_to TEXT[] default {}, created_at, updated_at.
--     No deleted_at: it lives and is replaced with its page (a replaced page's annotations stay on that page).
--
--   icid.projects              (changed)
--     + cost_per_sf_4in, cost_per_sf_7in   NUMERIC(10,4) NULL >= 0: the DOT assessment rates per square foot
--
-- Foreign keys to icid.users go from 20 to 26 (created_by and deleted_by on each of the three soft-deleted tables);
-- count_user_references gains non_assessable_areas, features and segments.
--
-- Run in the Supabase SQL editor, after 033, BEFORE deploying the code for it (the flag reads name defect_codes).
-- The code before it keeps working after it. Everything between BEGIN and COMMIT is one transaction.

-- ============================================================
-- PRE-CHECKS (run individually before the migration; stop if any differs)
-- Results on 2026-10-15 are under each.
-- ============================================================

-- 1. Flags per thickness, deleted ones included (only 4in, 7in and 7in_as_4in):
--   SELECT surveyed_thickness, count(*) AS total, count(*) FILTER (WHERE deleted_at IS NULL) AS live
--   FROM icid.flags GROUP BY 1 ORDER BY 1;
--   2026-10-15: 4in | 17 | 10, 7in | 2 | 0

-- 2. Flags per non-assessable reason, deleted ones included (only the 9 current values, or NULL):
--   SELECT coalesce(surveyed_non_assessable_reason, '(null)') AS reason, count(*) AS total,
--          count(*) FILTER (WHERE deleted_at IS NULL) AS live
--   FROM icid.flags GROUP BY 1 ORDER BY 1;
--   2026-10-15: (null) | 19 | 10

-- 3. pir_pages exists and the four new tables don't:
--   SELECT to_regclass('icid.pir_pages'), to_regclass('icid.non_assessable_areas'), to_regclass('icid.features'),
--          to_regclass('icid.segments'), to_regclass('icid.pir_annotations');
--   2026-10-15: icid.pir_pages | NULL | NULL | NULL | NULL

-- 4. The new columns don't exist:
--   SELECT table_name, column_name FROM information_schema.columns
--   WHERE table_schema = 'icid'
--     AND ((table_name = 'flags' AND column_name = 'defect_codes')
--       OR (table_name = 'projects' AND column_name IN ('cost_per_sf_4in', 'cost_per_sf_7in')));
--   2026-10-15: no rows

-- 5. The reason CHECK has the name dropped below:
--   SELECT conname FROM pg_constraint
--   WHERE conrelid = 'icid.flags'::regclass AND conname = 'chk_flags_non_assessable_reason';
--   2026-10-15: chk_flags_non_assessable_reason

-- 6. Foreign keys to users:
--   SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND confrelid = 'icid.users'::regclass;
--   2026-10-15: 20

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

-- 1. A flag's DOT defect codes
ALTER TABLE icid.flags
    ADD COLUMN defect_codes TEXT[] NOT NULL DEFAULT '{}',
    ADD CONSTRAINT chk_flags_defect_codes
        CHECK (defect_codes <@ ARRAY['1', '2', '3', '4', '5', '6A', '6B', '6C']::TEXT[]);

-- 2. The non-assessable reasons: every value it had, plus the R1e.1 survey's
ALTER TABLE icid.flags DROP CONSTRAINT chk_flags_non_assessable_reason;
ALTER TABLE icid.flags ADD CONSTRAINT chk_flags_non_assessable_reason CHECK (surveyed_non_assessable_reason IS NULL OR
    surveyed_non_assessable_reason IN ('tree_damage', 'city_tree', 'transition', 'no_driveway', 'hydrant',
                                       'fronting_city', 'utility', 'settlement', 'other',
                                       'tree', 'driveway', 'grass', 'dirt', 'existing_sidewalk', 'asphalt',
                                       'tree_pit', 'landmark', 'vault', 'subway', 'scaffolding',
                                       'other_perm_obstruction', 'other_temp_obstruction'));

-- 3. The parts of a flag that are not assessed
CREATE TABLE icid.non_assessable_areas (
    non_assessable_area_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_id                 UUID NOT NULL REFERENCES icid.flags(id),
    display_order           INT NOT NULL,
    shape                   TEXT NOT NULL,
    dimensions              JSONB NOT NULL,
    sketch_x                REAL NOT NULL,
    sketch_y                REAL NOT NULL,
    rotation                REAL NOT NULL DEFAULT 0,
    reason                  TEXT NOT NULL,
    computed_quantity       NUMERIC NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by              UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at              TIMESTAMPTZ,
    deleted_by              UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_nas_display_order CHECK (display_order >= 1),
    CONSTRAINT chk_nas_shape CHECK (shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc')),
    CONSTRAINT chk_nas_reason CHECK (reason IN ('driveway', 'grass', 'dirt', 'existing_sidewalk', 'asphalt',
                                                'tree_pit', 'landmark', 'other'))
);
CREATE INDEX nas_flag_idx ON icid.non_assessable_areas (flag_id);
CREATE UNIQUE INDEX nas_flag_order_live_uq ON icid.non_assessable_areas (flag_id, display_order)
    WHERE deleted_at IS NULL;

-- 4. Point obstructions and sized features on a page
CREATE TABLE icid.features (
    feature_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_page_id        UUID NOT NULL REFERENCES icid.pir_pages(pir_page_id),
    flag_id            UUID REFERENCES icid.flags(id),
    feature_type       TEXT NOT NULL,
    area_impact        TEXT NOT NULL DEFAULT 'marker_only',
    sketch_x           REAL NOT NULL,
    sketch_y           REAL NOT NULL,
    rotation           REAL NOT NULL DEFAULT 0,
    shape              TEXT,
    dimensions         JSONB,
    label              TEXT,
    pay_item_id        UUID REFERENCES icid.contract_items(contract_item_id) ON DELETE SET NULL,
    computed_quantity  NUMERIC,
    reported_quantity  NUMERIC,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at         TIMESTAMPTZ,
    deleted_by         UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_features_type CHECK (feature_type IN (
        'manhole', 'grate', 'utility_cover', 'hydrant', 'fire_alarm', 'tree_pit', 'meter', 'sign_or_meter',
        'mail_box', 'telephone', 'cellar_door', 'pole', 'detectable_warning', 'other')),
    CONSTRAINT chk_features_area_impact CHECK (area_impact IN ('marker_only', 'subtract', 'sits_on_top')),
    CONSTRAINT chk_features_shape
        CHECK (shape IS NULL OR shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc', 'circle')),
    CONSTRAINT chk_features_shape_dimensions CHECK ((shape IS NULL) = (dimensions IS NULL)),
    CONSTRAINT chk_features_sized_impact CHECK (area_impact = 'marker_only' OR shape IS NOT NULL),
    CONSTRAINT chk_features_subtract_flag CHECK (area_impact <> 'subtract' OR flag_id IS NOT NULL),
    CONSTRAINT chk_features_reported_quantity CHECK (reported_quantity IS NULL OR reported_quantity > 0)
);
CREATE INDEX features_page_idx ON icid.features (pir_page_id);
CREATE INDEX features_live_idx ON icid.features (pir_page_id) WHERE deleted_at IS NULL;
CREATE INDEX features_flag_live_idx ON icid.features (flag_id) WHERE deleted_at IS NULL AND flag_id IS NOT NULL;

-- 5. Curb runs on a page
CREATE TABLE icid.segments (
    segment_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_page_id   UUID NOT NULL REFERENCES icid.pir_pages(pir_page_id),
    segment_type  TEXT NOT NULL,
    points        JSONB NOT NULL,
    length_ft     NUMERIC,
    pay_item_id   UUID REFERENCES icid.contract_items(contract_item_id) ON DELETE SET NULL,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at    TIMESTAMPTZ,
    deleted_by    UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_segments_type CHECK (segment_type IN (
        'curb_concrete', 'curb_concrete_depressed', 'curb_steel_faced', 'curb_steel_faced_corner', 'curb_granite',
        'other')),
    CONSTRAINT chk_segments_points CHECK (jsonb_typeof(points) = 'array'),
    CONSTRAINT chk_segments_length CHECK (length_ft IS NULL OR length_ft >= 0)
);
CREATE INDEX segments_page_idx ON icid.segments (pir_page_id);
CREATE INDEX segments_live_idx ON icid.segments (pir_page_id) WHERE deleted_at IS NULL;

-- 6. A page's title block, one row per page
CREATE TABLE icid.pir_annotations (
    pir_page_id                  UUID PRIMARY KEY REFERENCES icid.pir_pages(pir_page_id),
    contract_no                  TEXT,
    violation_no                 TEXT,
    boro                         TEXT,
    property_address             TEXT,
    block                        TEXT,
    lot                          TEXT,
    cross_street_left            TEXT,
    cross_street_right           TEXT,
    frontage_street              TEXT,
    tax_map_width_ft             NUMERIC(10,2),
    cc_curb_to_curb_ft           NUMERIC(10,2),
    property_status              TEXT,
    corner_property_status       TEXT,
    violation_not_removed_due_to TEXT[] NOT NULL DEFAULT '{}',
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_pir_annotations_boro
        CHECK (boro IS NULL OR boro IN ('Manhattan', 'Bronx', 'Brooklyn', 'Queens', 'Staten Island')),
    CONSTRAINT chk_pir_annotations_property_status CHECK (property_status IS NULL OR
        property_status IN ('all_defects_corrected', 'some_defects_corrected', 'no_work_done')),
    CONSTRAINT chk_pir_annotations_corner_status CHECK (corner_property_status IS NULL OR
        corner_property_status IN ('ped_ramp_installed', 'no_ped_ramp_exists', 'existing_ped_ramp')),
    CONSTRAINT chk_pir_annotations_widths CHECK ((tax_map_width_ft IS NULL OR tax_map_width_ft >= 0)
                                                 AND (cc_curb_to_curb_ft IS NULL OR cc_curb_to_curb_ft >= 0))
);

-- 7. A project's DOT assessment rates
ALTER TABLE icid.projects
    ADD COLUMN cost_per_sf_4in NUMERIC(10,4),
    ADD COLUMN cost_per_sf_7in NUMERIC(10,4),
    ADD CONSTRAINT chk_projects_assessment_rates CHECK ((cost_per_sf_4in IS NULL OR cost_per_sf_4in >= 0)
                                                        AND (cost_per_sf_7in IS NULL OR cost_per_sf_7in >= 0));

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The four tables are there and empty:
--   SELECT (SELECT count(*) FROM icid.non_assessable_areas), (SELECT count(*) FROM icid.features),
--          (SELECT count(*) FROM icid.segments), (SELECT count(*) FROM icid.pir_annotations);
--   Expected: 0 | 0 | 0 | 0

-- B. Every flag has no defect codes, and the two CHECKs are in place:
--   SELECT count(*) FILTER (WHERE defect_codes <> '{}') FROM icid.flags;
--   Expected: 0
--   SELECT conname FROM pg_constraint WHERE conrelid = 'icid.flags'::regclass
--     AND conname IN ('chk_flags_defect_codes', 'chk_flags_non_assessable_reason') ORDER BY 1;
--   Expected: chk_flags_defect_codes, chk_flags_non_assessable_reason

-- C. Foreign keys to users:
--   SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND confrelid = 'icid.users'::regclass;
--   Expected: 26

-- D. The assessment rates are there and unset:
--   SELECT count(*) FILTER (WHERE cost_per_sf_4in IS NOT NULL OR cost_per_sf_7in IS NOT NULL) FROM icid.projects;
--   Expected: 0

-- ============================================================
-- ROLLBACK (only while the new tables are empty and no flag has a defect code or a new reason)
-- ============================================================

-- BEGIN;
-- ALTER TABLE icid.projects DROP CONSTRAINT chk_projects_assessment_rates,
--     DROP COLUMN cost_per_sf_4in, DROP COLUMN cost_per_sf_7in;
-- DROP TABLE icid.pir_annotations;
-- DROP TABLE icid.segments;
-- DROP TABLE icid.features;
-- DROP TABLE icid.non_assessable_areas;
-- ALTER TABLE icid.flags DROP CONSTRAINT chk_flags_non_assessable_reason;
-- ALTER TABLE icid.flags ADD CONSTRAINT chk_flags_non_assessable_reason CHECK (surveyed_non_assessable_reason IS NULL OR
--     surveyed_non_assessable_reason IN ('tree_damage', 'city_tree', 'transition', 'no_driveway', 'hydrant',
--                                        'fronting_city', 'utility', 'settlement', 'other'));
-- ALTER TABLE icid.flags DROP CONSTRAINT chk_flags_defect_codes, DROP COLUMN defect_codes;
-- COMMIT;
