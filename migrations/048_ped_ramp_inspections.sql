-- migrations/048_ped_ramp_inspections.sql
--
-- Pedramp Phase 3, S1: the inspection workflow, and DOT's choice lists.
--   * icid.ped_ramp_inspections: one row per corner inspection, the review and signature wrapper around the
--     corner (sections 1-4) and its ramps' attributes (section 5), which stay where they are. Two stages: an
--     inspector submits (signing), the RE approves (signing) or returns it to draft with a comment. One live
--     (not approved) inspection per corner. dot_pdf_path is the DOT form filled at approval, kept in the
--     pedramp-exports bucket (050b). Four more foreign keys to users: inspector_uuid, re_uuid, returned_by,
--     created_by (count_user_references counts them).
--   * DOT's choice lists (Pedestrian Ramp Inspection Form v1.6's dropdowns): entity's 13 options, curb material
--     without cobblestone, DWS colour without yellow (dark on light and light on dark added), DWS material with
--     preformed plastic split into embedded and surface applied, cast in place surface applied, and concrete tile
--     (other dropped). ONE-WAY: a value dropped here can't come back without a data migration. Checked live on
--     2026-10-20: the one corner has entity and curb_material NULL, and no ramp has attributes.
--   * ped_ramp_attributes.ramp_location_code: the position on the DOT form's "ramp location at corner" diagram
--     (0-22). Nullable; nothing sets it for the pilot, and the export leaves the diagram blank.
--
-- Run before deploying the S1 code. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.ped_ramp_inspections; ALTER TABLE icid.ped_ramp_attributes
-- DROP COLUMN ramp_location_code; the choice lists can only go back to 047's if no row holds a value 047 lacks.

BEGIN;

CREATE TABLE IF NOT EXISTS icid.ped_ramp_inspections (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corner_id                  UUID NOT NULL REFERENCES icid.ped_ramp_corners(corner_id),
    status                     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'approved')),
    -- The inspector's answer to "are all ADA requirements met?"; FALSE prompts a TIF per failing ramp
    requirements_met           BOOLEAN,
    -- Submit: who signed, when, and the copy of their signature the inspection keeps
    inspector_uuid             UUID REFERENCES icid.users(uuid),
    submitted_at               TIMESTAMPTZ,
    inspector_signed_at        TIMESTAMPTZ,
    inspector_signature_path   TEXT,
    -- Approve: the RE, and the copy of their signature
    re_uuid                    UUID REFERENCES icid.users(uuid),
    re_signed_at               TIMESTAMPTZ,
    re_signature_path          TEXT,
    -- Return: back to draft, with the RE's required comment
    return_comment             TEXT,
    returned_at                TIMESTAMPTZ,
    returned_by                UUID REFERENCES icid.users(uuid),
    -- The DOT form filled at approval (pedramp-exports bucket)
    dot_pdf_path               TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                 UUID NOT NULL REFERENCES icid.users(uuid),
    -- Past draft, the inspector's signature is there
    CONSTRAINT chk_ped_ramp_inspections_submitted CHECK (status = 'draft' OR (
        inspector_uuid IS NOT NULL AND submitted_at IS NOT NULL AND inspector_signed_at IS NOT NULL
        AND inspector_signature_path IS NOT NULL)),
    -- Approved, the RE's is too
    CONSTRAINT chk_ped_ramp_inspections_approved CHECK (status <> 'approved' OR (
        re_uuid IS NOT NULL AND re_signed_at IS NOT NULL AND re_signature_path IS NOT NULL))
);
-- One live inspection per corner at a time (re-inspection is post-pilot)
CREATE UNIQUE INDEX IF NOT EXISTS uq_ped_ramp_inspections_live_corner
    ON icid.ped_ramp_inspections (corner_id) WHERE status <> 'approved';
CREATE INDEX IF NOT EXISTS idx_ped_ramp_inspections_corner ON icid.ped_ramp_inspections (corner_id);

-- DOT's choice lists (one-way; see above)
ALTER TABLE icid.ped_ramp_corners DROP CONSTRAINT IF EXISTS ped_ramp_corners_entity_check;
ALTER TABLE icid.ped_ramp_corners ADD CONSTRAINT ped_ramp_corners_entity_check CHECK (entity IN (
    'ddc_city_agency_capital', 'ddc_landmark_ped_ramp', 'ddc_dot_ped_ramp', 'ddc_prior_notice', 'ddc_ta_ped_ramp',
    'dep', 'dob', 'dpr', 'edc', 'mta', 'nysdot', 'sca', 'other'));
ALTER TABLE icid.ped_ramp_corners DROP CONSTRAINT IF EXISTS ped_ramp_corners_curb_material_check;
ALTER TABLE icid.ped_ramp_corners ADD CONSTRAINT ped_ramp_corners_curb_material_check CHECK (curb_material IN (
    'steel_faced', 'concrete', 'concrete_and_gutter', 'granite', 'bluestone', 'other'));
ALTER TABLE icid.ped_ramp_attributes DROP CONSTRAINT IF EXISTS ped_ramp_attributes_dws_color_check;
ALTER TABLE icid.ped_ramp_attributes ADD CONSTRAINT ped_ramp_attributes_dws_color_check CHECK (dws_color IN (
    'red', 'white', 'dark_on_light', 'light_on_dark', 'other'));
ALTER TABLE icid.ped_ramp_attributes DROP CONSTRAINT IF EXISTS ped_ramp_attributes_dws_material_check;
ALTER TABLE icid.ped_ramp_attributes ADD CONSTRAINT ped_ramp_attributes_dws_material_check CHECK (dws_material IN (
    'preformed_plastic_embedded', 'preformed_plastic_surface_applied', 'cast_in_place_surface_applied', 'cast_iron',
    'stone', 'concrete_tile'));

-- The DOT diagram's ramp position (blank for the pilot)
ALTER TABLE icid.ped_ramp_attributes ADD COLUMN IF NOT EXISTS ramp_location_code SMALLINT
    CONSTRAINT chk_ped_ramp_attributes_location_code CHECK (ramp_location_code BETWEEN 0 AND 22);

COMMIT;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT count(*) FROM icid.ped_ramp_inspections;                                      -- 0
-- SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conname IN ('ped_ramp_corners_entity_check', 'ped_ramp_corners_curb_material_check',
--                    'ped_ramp_attributes_dws_color_check', 'ped_ramp_attributes_dws_material_check');  -- 4 rows, the new lists
