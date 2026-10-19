-- migrations/047_ped_ramp_inspection.sql
--
-- Pedramp 2a: DOT pedestrian ramp inspection data (NYC DOT Pedestrian Ramp Inspection Form, version 1.6).
--   * icid.ped_ramp_corners: one row per corner inspection, the form's sections 1-3 (inspection, location,
--     construction). Soft-deleted, with created_by and deleted_by (two more foreign keys to users).
--   * properties.corner_id: the corner a ped ramp site is at (a sidewalk has none: CHECK chk_properties_corner_kind).
--   * icid.ped_ramp_attributes: one row per ped ramp site (keyed by property_id), the form's section 5 for that ramp:
--     the fields every ramp has, then those of its ramp type (perpendicular, parallel, cut-through), all nullable.
--     A cut-through's two ramps are two sites, each with its own row; its length and the clearance between its DWS
--     are kept on both. Slopes are percent, lengths, widths, offsets and the curb reveal inches, as the form has them.
--
-- The code that reads properties selects corner_id, so run this before deploying it. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.ped_ramp_attributes; ALTER TABLE icid.properties DROP
-- CONSTRAINT chk_properties_corner_kind, DROP COLUMN corner_id; DROP TABLE icid.ped_ramp_corners;

BEGIN;

CREATE TABLE IF NOT EXISTS icid.ped_ramp_corners (
    corner_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                    TEXT NOT NULL REFERENCES icid.projects(project_id),
    -- 1. Inspection information
    inspected_by                  TEXT,
    inspection_date               DATE,
    entity                        TEXT CHECK (entity IN ('dob', 'other')),
    entity_if_other               TEXT,
    -- 2. Location information
    boro                          icid.boro NOT NULL,
    dot_corner_id                 TEXT,
    corner_number                 TEXT,
    north_south_street            TEXT NOT NULL,
    east_west_street              TEXT NOT NULL,
    traffic_control_ns            TEXT CHECK (traffic_control_ns IN ('signal', 'signal_aps', 'stop_yield', 'none')),
    traffic_control_ew            TEXT CHECK (traffic_control_ew IN ('signal', 'signal_aps', 'stop_yield', 'none')),
    corner_type                   TEXT CHECK (corner_type IN ('intersection_corner', 'island_median', 'mid_block',
                                                              'top_of_t')),
    corner_direction              TEXT CHECK (corner_direction IN ('SW', 'NW', 'NE', 'SE', 'N', 'S', 'E', 'W')),
    number_of_ramps               INT CHECK (number_of_ramps BETWEEN 1 AND 3),
    t_intersection                BOOLEAN,
    painted_roadway               BOOLEAN,
    -- 3. Construction information
    contractor_name               TEXT,
    permittee_id                  TEXT,
    fms_id                        TEXT,
    permit_number                 TEXT,
    construction_completion_date  DATE,
    contract_type                 TEXT CHECK (contract_type IN ('simple', 'complex')),
    curb_material                 TEXT CHECK (curb_material IN ('steel_faced', 'concrete', 'concrete_and_gutter',
                                                                'granite', 'bluestone', 'cobblestone', 'other')),
    sidewalk_material             TEXT CHECK (sidewalk_material IN ('concrete', 'pigmented_concrete', 'bluestone',
                                                                    'cobblestone', 'other')),
    comments                      TEXT,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                    UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at                    TIMESTAMPTZ,
    deleted_by                    UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_ped_ramp_corners_project ON icid.ped_ramp_corners (project_id) WHERE deleted_at IS NULL;

ALTER TABLE icid.properties ADD COLUMN IF NOT EXISTS corner_id UUID REFERENCES icid.ped_ramp_corners(corner_id);
ALTER TABLE icid.properties DROP CONSTRAINT IF EXISTS chk_properties_corner_kind;
ALTER TABLE icid.properties ADD CONSTRAINT chk_properties_corner_kind CHECK (corner_id IS NULL OR kind = 'ped_ramp');
CREATE INDEX IF NOT EXISTS idx_properties_corner ON icid.properties (corner_id) WHERE corner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS icid.ped_ramp_attributes (
    property_id                       UUID PRIMARY KEY REFERENCES icid.properties(id) ON DELETE CASCADE,
    -- Every ramp
    ramp_id_label                     TEXT,
    is_there_ponding                  BOOLEAN,
    maximum_curb_reveal_at_ramp_in    NUMERIC CHECK (maximum_curb_reveal_at_ramp_in >= 0),
    clear_space_within_crosswalk      BOOLEAN,
    dws_length_min_24_inch            BOOLEAN,
    dws_full_ramp_width               BOOLEAN,
    dws_aligned_with_ramp             TEXT CHECK (dws_aligned_with_ramp IN ('yes', 'no', 'na')),
    dws_left_offset_from_curb_in      NUMERIC CHECK (dws_left_offset_from_curb_in >= 0),
    dws_right_offset_from_curb_in     NUMERIC CHECK (dws_right_offset_from_curb_in >= 0),
    dws_color                         TEXT CHECK (dws_color IN ('red', 'white', 'yellow', 'other')),
    dws_material                      TEXT CHECK (dws_material IN ('preformed_plastic', 'cast_in_place_plastic',
                                                                    'cast_iron', 'stone', 'other')),
    construction                      TEXT CHECK (construction IN ('existing_ramp', 'ramp_removed', 'new_install',
                                                                    'upgrade')),
    action_taken                      TEXT CHECK (action_taken IN ('ada_compliant_constructed', 'max_extent_feasible',
                                                                    'ada_compliant_exists', 'ramp_removed', 'other')),
    comments                          TEXT,
    -- Perpendicular (and, for the ramp, gutter and its running/cross slopes and width, cut-through too)
    left_flare_traversable_path       BOOLEAN,
    right_flare_traversable_path      BOOLEAN,
    outside_of_parallel_travel_lane   BOOLEAN,
    ramp_running_slope_pct            NUMERIC,
    ramp_cross_slope_pct              NUMERIC,
    ramp_length_in                    NUMERIC CHECK (ramp_length_in >= 0),
    ramp_width_in                     NUMERIC CHECK (ramp_width_in >= 0),
    landing_running_slope_pct         NUMERIC,
    landing_cross_slope_pct           NUMERIC,
    landing_length_in                 NUMERIC CHECK (landing_length_in >= 0),
    landing_width_in                  NUMERIC CHECK (landing_width_in >= 0),
    left_flare_slope_pct              NUMERIC,
    right_flare_slope_pct             NUMERIC,
    gutter_cross_slope_pct            NUMERIC,
    gutter_flow_slope_pct             NUMERIC,
    left_sidewalk_approach_width_in   NUMERIC CHECK (left_sidewalk_approach_width_in >= 0),
    left_sidewalk_running_slope_pct   NUMERIC,
    left_sidewalk_cross_slope_pct     NUMERIC,
    right_sidewalk_approach_width_in  NUMERIC CHECK (right_sidewalk_approach_width_in >= 0),
    right_sidewalk_running_slope_pct  NUMERIC,
    right_sidewalk_cross_slope_pct    NUMERIC,
    -- Parallel
    bottom_landing_running_slope_pct  NUMERIC,
    bottom_landing_cross_slope_pct    NUMERIC,
    bottom_landing_length_in          NUMERIC CHECK (bottom_landing_length_in >= 0),
    bottom_landing_width_in           NUMERIC CHECK (bottom_landing_width_in >= 0),
    left_ramp_running_slope_pct       NUMERIC,
    left_ramp_cross_slope_pct         NUMERIC,
    left_ramp_length_in               NUMERIC CHECK (left_ramp_length_in >= 0),
    left_ramp_width_in                NUMERIC CHECK (left_ramp_width_in >= 0),
    right_ramp_running_slope_pct      NUMERIC,
    right_ramp_cross_slope_pct        NUMERIC,
    right_ramp_length_in              NUMERIC CHECK (right_ramp_length_in >= 0),
    right_ramp_width_in               NUMERIC CHECK (right_ramp_width_in >= 0),
    -- Cut-through (kept on both of its ramps' rows)
    cut_through_length_in             NUMERIC CHECK (cut_through_length_in >= 0),
    min_clearance_between_dws_in      NUMERIC CHECK (min_clearance_between_dws_in >= 0),
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
