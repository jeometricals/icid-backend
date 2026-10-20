-- migrations/049_ped_ramp_tifs.sql
--
-- Pedramp Phase 3, S3: Technical Infeasibility Forms (NYC DOT Pedestrian Ramp Technical Infeasibility /
-- Structurally Impractical Form, version 1.9). One TIF per non-compliant ramp of an inspection: it names the ramp
-- (property_id, a ped ramp of the inspection's corner; the route checks) and the inspection. An inspector or the RE
-- drafts it; only the RE signs and approves it, giving their name and New York licence number then. An inspection
-- can't be approved while one of its TIFs is a draft (the route answers 409).
--
-- The columns follow the form's sections. What the form's section 3 shows of the corner (borough, streets, corner
-- direction, T-intersection, corner type) is read from the corner at export; only what the TIF captures on its own
-- is stored (DOT's corner and ramp ids, prefilled from the corner and the ramp's label, and the ramp construction).
-- dot_project_id is DOT's "Project ID" text, not icid's project. Section 9 (site design / layout) is an uploaded
-- image (050, kind 'layout'); the form's "For internal use only" block is DOT's and never filled.
--
-- Two more foreign keys to users: signed_by_uuid, created_by (count_user_references counts them).
-- Run after 048, before deploying the S3 code. Safe to run twice.
--
-- Rollback (after rolling the code back): DROP TABLE icid.ped_ramp_tifs;

BEGIN;

CREATE TABLE IF NOT EXISTS icid.ped_ramp_tifs (
    id                                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id                           UUID NOT NULL REFERENCES icid.properties(id),
    inspection_id                         UUID NOT NULL REFERENCES icid.ped_ramp_inspections(id),
    status                                TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved')),
    -- 1. Submission information
    submitted_by                          TEXT,
    submission_date                       DATE,
    application_tracking_no               TEXT,
    sequence_no                           TEXT,
    permittee_name                        TEXT,
    permittee_id                          TEXT,
    business_name                         TEXT,
    street_address                        TEXT,
    city                                  TEXT,
    state                                 TEXT,
    zip_code                              TEXT,
    email                                 TEXT,
    phone                                 TEXT,
    -- 2. Project information: the category ticked, and the agency or entity named beside it
    project_category                      TEXT CHECK (project_category IN ('city_agency', 'state_agency',
                                                                           'transit_authority', 'other')),
    project_category_name                 TEXT,
    dot_project_id                        TEXT,
    type_of_project                       TEXT,
    -- 3. Location information (the rest is the corner's)
    dot_corner_id                         TEXT,
    dot_ramp_id                           TEXT,
    ramp_construction                     TEXT CHECK (ramp_construction IN ('new_installation', 'upgrade')),
    -- 4. Why it is technically infeasible (check all that apply)
    reason_limited_row                    BOOLEAN NOT NULL DEFAULT false,
    reason_existing_topography            BOOLEAN NOT NULL DEFAULT false,
    reason_existing_utilities             BOOLEAN NOT NULL DEFAULT false,
    reason_structures                     BOOLEAN NOT NULL DEFAULT false,
    reason_trees_natural                  BOOLEAN NOT NULL DEFAULT false,
    reason_nyct_structures                BOOLEAN NOT NULL DEFAULT false,
    reason_historic_features              BOOLEAN NOT NULL DEFAULT false,
    reason_other                          BOOLEAN NOT NULL DEFAULT false,
    reason_other_text                     TEXT,
    -- 5. Why the requirements can't be achieved
    why_requirements_cannot_be_achieved   TEXT,
    -- 6. Ramp elements that don't meet the minimum ADA standards (check all that apply)
    element_ramp_width                    BOOLEAN NOT NULL DEFAULT false,
    element_running_slope                 BOOLEAN NOT NULL DEFAULT false,
    element_cross_slope                   BOOLEAN NOT NULL DEFAULT false,
    element_flare_slopes                  BOOLEAN NOT NULL DEFAULT false,
    element_dws                           BOOLEAN NOT NULL DEFAULT false,
    element_landing_dimensions            BOOLEAN NOT NULL DEFAULT false,
    element_landing_slopes                BOOLEAN NOT NULL DEFAULT false,
    element_gutter_cross_slope            BOOLEAN NOT NULL DEFAULT false,
    element_gutter_flow_slope             BOOLEAN NOT NULL DEFAULT false,
    element_other                         BOOLEAN NOT NULL DEFAULT false,
    element_other_text                    TEXT,
    -- 7. Investigation
    investigation_confirmed               BOOLEAN NOT NULL DEFAULT false,
    additional_explanation                TEXT,
    -- 10. Statement and signature: the RE, at approval
    signer_name                           TEXT,
    signer_ny_license_no                  TEXT,
    signer_signature_path                 TEXT,
    signed_at                             TIMESTAMPTZ,
    signed_by_uuid                        UUID REFERENCES icid.users(uuid),
    created_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                            UUID NOT NULL REFERENCES icid.users(uuid),
    -- Approved, it is signed: by whom, their name and licence, the copy of their signature, and when
    CONSTRAINT chk_ped_ramp_tifs_signed CHECK (status <> 'approved' OR (
        signer_name IS NOT NULL AND signer_ny_license_no IS NOT NULL AND signer_signature_path IS NOT NULL
        AND signed_at IS NOT NULL AND signed_by_uuid IS NOT NULL))
);
-- One TIF per ramp per inspection
CREATE UNIQUE INDEX IF NOT EXISTS uq_ped_ramp_tifs_ramp_inspection ON icid.ped_ramp_tifs (property_id, inspection_id);
CREATE INDEX IF NOT EXISTS idx_ped_ramp_tifs_inspection ON icid.ped_ramp_tifs (inspection_id);

COMMIT;

-- ============================================================
-- POST-CHECK
-- ============================================================
-- SELECT count(*) FROM icid.ped_ramp_tifs;   -- 0
