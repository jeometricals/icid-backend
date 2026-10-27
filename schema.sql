-- schema.sql
-- Authoritative DDL for the icid schema.
--
-- Data model: IDR (Inspector Daily Diary). One icid.idrs row per inspector
-- per project per day holds the shared header; icid.idr_reports holds the
-- typed reports (General, addenda, ...) filed under it. Introduced in
-- migrations/004_idr_refactor.sql (Phase R, Slice R1).
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS icid;

------------------------------------------------------------
-- CLIENT
------------------------------------------------------------
CREATE TABLE icid.clients (
    client_id       TEXT PRIMARY KEY,          
    client_username TEXT NOT NULL,
    client_name     TEXT NOT NULL,
    client_email    TEXT,
    client_phone    TEXT,
    client_role     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_clients_client_id ON icid.clients(client_id);
CREATE INDEX idx_clients_client_username ON icid.clients(client_username);

------------------------------------------------------------
-- USER
------------------------------------------------------------
CREATE TABLE icid.users (
    uuid            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT NOT NULL,
    first_name      TEXT,
    last_name       TEXT,
    phone_number    TEXT,
    client_id       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    password_hash   TEXT,
    role            TEXT,
    is_demo         BOOLEAN NOT NULL DEFAULT false,
    signature_path   TEXT,
    signature_type   TEXT,
    signature_set_at TIMESTAMPTZ,
    deleted_at       TIMESTAMPTZ,
    deleted_by       UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT fk_users_client
        FOREIGN KEY (client_id) REFERENCES icid.clients(client_id),
    CONSTRAINT chk_users_signature_type CHECK (signature_type IN ('drawn', 'uploaded'))
);

CREATE INDEX idx_users_uuid ON icid.users(uuid);
-- One account per email, whatever its capitals, among users who aren't deleted (a deleted user's email is free
-- again; migrations/028); also serves sign-in's lookup by lower(email)
CREATE UNIQUE INDEX idx_users_email_lower ON icid.users (lower(email)) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_is_demo ON icid.users(is_demo) WHERE is_demo = true;

------------------------------------------------------------
-- PROJECT
------------------------------------------------------------
CREATE TABLE icid.projects (
    project_id          TEXT PRIMARY KEY,
    project_name        TEXT NOT NULL,
    project_description TEXT,
    registration_code   TEXT,
    borough             TEXT,
    status              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The DOT assessment rates per square foot of 4" and 7" sidewalk (migrations/034)
    cost_per_sf_4in     NUMERIC(10,4),
    cost_per_sf_7in     NUMERIC(10,4),
    CONSTRAINT chk_projects_assessment_rates CHECK ((cost_per_sf_4in IS NULL OR cost_per_sf_4in >= 0)
                                                    AND (cost_per_sf_7in IS NULL OR cost_per_sf_7in >= 0))
);

CREATE INDEX idx_projects_project_id ON icid.projects(project_id);

------------------------------------------------------------
-- PROJECT_USER (assignment table)
------------------------------------------------------------
CREATE TABLE icid.project_users (
    project_id   TEXT NOT NULL,
    user_uuid    UUID NOT NULL,
    user_role    TEXT,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    role         TEXT NOT NULL DEFAULT 'inspector',
    PRIMARY KEY (project_id, user_uuid, role),
    CONSTRAINT chk_project_users_role CHECK (role IN ('inspector', 'oe', 're')),
    CONSTRAINT fk_project_users_project
        FOREIGN KEY (project_id) REFERENCES icid.projects(project_id),
    CONSTRAINT fk_project_users_user
        FOREIGN KEY (user_uuid) REFERENCES icid.users(uuid)
);

CREATE INDEX idx_project_users_project ON icid.project_users(project_id);
CREATE INDEX idx_project_users_user_uuid ON icid.project_users(user_uuid);

------------------------------------------------------------
-- PROJECT_CLIENT (extra clients on project)
------------------------------------------------------------
CREATE TABLE icid.project_clients (
    project_id   TEXT NOT NULL,
    client_id    TEXT NOT NULL,
    client_role  TEXT,
    PRIMARY KEY (project_id, client_id),
    CONSTRAINT fk_project_clients_project
        FOREIGN KEY (project_id) REFERENCES icid.projects(project_id),
    CONSTRAINT fk_project_clients_client
        FOREIGN KEY (client_id) REFERENCES icid.clients(client_id)
);

CREATE INDEX idx_project_clients_project ON icid.project_clients(project_id);
CREATE INDEX idx_project_clients_client ON icid.project_clients(client_id);

------------------------------------------------------------
-- FORM TEMPLATE
------------------------------------------------------------
CREATE TABLE icid.form_templates (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    form_template_id  TEXT UNIQUE NOT NULL,
    form_name         TEXT NOT NULL,
    form_description  TEXT,
    form_status       TEXT,
    mandatory_forms   TEXT,
    optional_forms    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

------------------------------------------------------------
-- IDR (one per inspector per project per day)
------------------------------------------------------------
CREATE TABLE icid.idrs (
    idr_id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id             TEXT NOT NULL REFERENCES icid.projects(project_id),
    reporter_uuid          UUID NOT NULL REFERENCES icid.users(uuid),
    report_date            DATE NOT NULL,
    work_start_time        TIME NULL,
    work_end_time          TIME NULL,
    inspector_start_time   TIME NULL,
    inspector_end_time     TIME NULL,
    temp_low               NUMERIC(4,1) NULL,
    temp_high              NUMERIC(4,1) NULL,
    weather_am             TEXT NULL,
    weather_pm             TEXT NULL,
    total_pages            INTEGER NULL,
    has_dismissed_auto_general BOOLEAN NOT NULL DEFAULT false,
    status                 TEXT NOT NULL DEFAULT 'draft',
    submitted_at           TIMESTAMPTZ NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    inspector_signature_path TEXT NULL,
    inspector_signed_at      TIMESTAMPTZ NULL,
    idr_number             TEXT,
    stage1_reviewer_uuid   UUID REFERENCES icid.users(uuid),
    stage1_accepted_at     TIMESTAMPTZ,
    stage1_reviewed_at     TIMESTAMPTZ,
    re_reviewer_uuid       UUID REFERENCES icid.users(uuid),
    stage2_accepted_at     TIMESTAMPTZ,
    re_signature_path      TEXT,
    re_signed_at           TIMESTAMPTZ,
    return_reason          TEXT,
    returned_from          TEXT,
    deleted_at             TIMESTAMPTZ,
    deleted_by             UUID REFERENCES icid.users(uuid),
    -- Its default flag scope (migrations/037): the live flags on this task order's properties; none without one
    task_order_id          UUID REFERENCES icid.task_orders(id),
    -- Its last admin unlock (migrations/052): when, and by which admin
    last_unlocked_at       TIMESTAMPTZ,
    last_unlocked_by       UUID REFERENCES icid.users(uuid),
    CONSTRAINT chk_idrs_status CHECK (status IN ('draft', 'submitted', 'stage1_review', 'stage2_review', 'approved', 'returned', 'deleted')),
    CONSTRAINT chk_idrs_returned_from CHECK (returned_from IN ('stage1', 'stage2'))
);

-- One IDR per inspector per project per day among IDRs that aren't deleted; a soft delete frees the day
CREATE UNIQUE INDEX uq_idrs_project_reporter_date
    ON icid.idrs(project_id, reporter_uuid, report_date)
    WHERE deleted_at IS NULL;

CREATE INDEX idx_idrs_project_status ON icid.idrs(project_id, status) WHERE deleted_at IS NULL;
CREATE INDEX idx_idrs_status ON icid.idrs(status) WHERE deleted_at IS NULL;
CREATE INDEX idx_idrs_reporter ON icid.idrs(reporter_uuid);
CREATE INDEX idx_idrs_task_order ON icid.idrs (task_order_id) WHERE task_order_id IS NOT NULL;
-- An IDR number is used once per project among IDRs that aren't deleted; a soft delete frees it
CREATE UNIQUE INDEX uq_idrs_project_number
    ON icid.idrs(project_id, idr_number)
    WHERE idr_number IS NOT NULL AND deleted_at IS NULL;

------------------------------------------------------------
-- IDR AUDIT (what was done to an IDR in review; migrations/019)
------------------------------------------------------------
CREATE TABLE icid.idr_audit (
    audit_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idr_id       UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    actor_uuid   UUID NOT NULL REFERENCES icid.users(uuid),
    action       TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_idr_audit_idr_created ON icid.idr_audit(idr_id, created_at DESC);

------------------------------------------------------------
-- IDR REPORT (typed report within an IDR; addenda link to a parent)
-- report_data holds the report's form; each entry of its payItems list carries an "id" (migrations/020)
------------------------------------------------------------
CREATE TABLE icid.idr_reports (
    report_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    idr_id                UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    report_type           TEXT NOT NULL,
    is_addendum           BOOLEAN NOT NULL DEFAULT false,
    parent_report_id      UUID NULL REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE,
    page_number           INTEGER NULL,
    report_data           JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_auto_generated     BOOLEAN NOT NULL DEFAULT false,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_idr_reports_parent CHECK (
        (is_addendum = false AND parent_report_id IS NULL)
        OR (is_addendum = true)
    )
);

CREATE INDEX idx_idr_reports_idr ON icid.idr_reports(idr_id);
CREATE INDEX idx_idr_reports_parent ON icid.idr_reports(parent_report_id) WHERE parent_report_id IS NOT NULL;
-- At most one Daily Site Patrol per IDR (migrations/053); and the pair a report site's foreign key points at
CREATE UNIQUE INDEX uq_idr_reports_one_dsp_per_idr ON icid.idr_reports (idr_id) WHERE report_type = 'DSP';
CREATE UNIQUE INDEX uq_idr_reports_report_idr ON icid.idr_reports (report_id, idr_id);

------------------------------------------------------------
-- IDR REPORT SITES (the sites a report imports and the work done at each; once per IDR; the IDR's flags and curb
-- segments are claimed from them; migrations/053)
------------------------------------------------------------
CREATE TABLE icid.idr_report_sites (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id        UUID NOT NULL,
    idr_id           UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    property_id      UUID NOT NULL REFERENCES icid.properties(id),
    work_type        TEXT NOT NULL CHECK (work_type IN ('excavation', 'concrete', 'pavement_restoration', 'other')),
    work_type_other  TEXT,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by         UUID NOT NULL REFERENCES icid.users(uuid),
    updated_at       TIMESTAMPTZ,
    updated_by       UUID REFERENCES icid.users(uuid),
    CONSTRAINT fk_idr_report_sites_report FOREIGN KEY (report_id, idr_id)
        REFERENCES icid.idr_reports (report_id, idr_id) ON DELETE CASCADE,
    CONSTRAINT chk_idr_report_sites_other CHECK (work_type <> 'other' OR work_type_other IS NOT NULL),
    CONSTRAINT uq_idr_report_sites_idr_property UNIQUE (idr_id, property_id)
);
CREATE INDEX idx_idr_report_sites_report ON icid.idr_report_sites (report_id);
CREATE INDEX idx_idr_report_sites_property ON icid.idr_report_sites (property_id);
-- updated_at / updated_by: the last change to the work, set by the site PATCH (migrations/055)

------------------------------------------------------------
-- IDR REPORT SITE PAY ITEMS (pay items entered by hand on an imported site; billed at approval as 'from_sites'
-- quantity rows; migrations/054)
------------------------------------------------------------
CREATE TABLE icid.idr_report_site_pay_items (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_site_id   UUID NOT NULL REFERENCES icid.idr_report_sites(id) ON DELETE CASCADE,
    contract_item_id UUID NOT NULL REFERENCES icid.contract_items(contract_item_id),
    quantity         NUMERIC NOT NULL,
    note             TEXT CHECK (note IS NULL OR char_length(note) <= 2000),
    added_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by         UUID NOT NULL REFERENCES icid.users(uuid),
    -- Slice X.1 (migration 056): the quantity replaces what the site's flags and curb segments come to for the pay item
    overrides_auto   BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT idr_report_site_pay_items_quantity_check CHECK (quantity > 0 OR (overrides_auto AND quantity >= 0))
);
CREATE INDEX idx_idr_report_site_pay_items_site ON icid.idr_report_site_pay_items (report_site_id);
CREATE INDEX idx_idr_report_site_pay_items_item ON icid.idr_report_site_pay_items (contract_item_id);
-- One override per site and pay item
CREATE UNIQUE INDEX uq_idr_report_site_pay_items_one_override
    ON icid.idr_report_site_pay_items (report_site_id, contract_item_id) WHERE overrides_auto;

-- At most one non-addendum General per IDR (migrations/005_one_general_per_idr.sql).
CREATE UNIQUE INDEX uq_idr_reports_one_gen_per_idr
    ON icid.idr_reports(idr_id)
    WHERE report_type = 'GEN' AND is_addendum = false;

------------------------------------------------------------
-- IDR FIELD EDIT (what a reviewer changed on an IDR; rows are only ever added; migrations/020)
------------------------------------------------------------
CREATE TABLE icid.idr_field_edits (
    edit_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idr_id        UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    report_id     UUID REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE,
    field_path    TEXT NOT NULL,
    edit_type     TEXT NOT NULL,
    old_value     JSONB,
    new_value     JSONB NOT NULL,
    editor_uuid   UUID NOT NULL REFERENCES icid.users(uuid),
    editor_stage  TEXT NOT NULL,
    edited_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_idr_field_edits_type CHECK (edit_type IN ('field_change', 'pay_item_revision', 'pay_item_add', 'pay_item_approve', 'truck_add')),
    CONSTRAINT chk_idr_field_edits_stage CHECK (editor_stage IN ('stage1', 'stage2')),
    CONSTRAINT chk_idr_field_edits_old_value CHECK ((old_value IS NULL) = (edit_type IN ('pay_item_add', 'truck_add')))
);

CREATE INDEX idx_idr_field_edits_idr ON icid.idr_field_edits(idr_id, edited_at);
CREATE INDEX idx_idr_field_edits_field ON icid.idr_field_edits(report_id, field_path);

------------------------------------------------------------
-- QUANTITY (an approved IDR's pay items, one row each; an IDR's rows are replaced as a set; migrations/025)
-- project_id, report_date and reporter_uuid are copies of the IDR's own
------------------------------------------------------------
CREATE TABLE icid.quantities (
    quantity_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     TEXT NOT NULL REFERENCES icid.projects(project_id),
    idr_id         UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    report_date    DATE NOT NULL,
    reporter_uuid  UUID NOT NULL,
    report_type    TEXT NOT NULL,
    pay_item_ref   TEXT,
    budget_code    TEXT,
    description    TEXT,
    amount         NUMERIC NOT NULL,
    unit           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- manual: a report's pay item; from_flags: the IDR's attributed flags rolled up per pay item (migrations/037)
    source         TEXT NOT NULL DEFAULT 'manual' CONSTRAINT chk_quantities_source CHECK (source IN ('manual', 'from_flags', 'from_segments', 'from_sites'))
);

CREATE INDEX idx_quantities_project_date ON icid.quantities(project_id, report_date);
CREATE INDEX idx_quantities_project_item ON icid.quantities(project_id, pay_item_ref);
CREATE INDEX idx_quantities_project_budget ON icid.quantities(project_id, budget_code);
CREATE INDEX idx_quantities_idr ON icid.quantities(idr_id);

------------------------------------------------------------
-- PROPERTY (a project's tax lots and its pedestrian ramps; soft-deleted like users and idrs; migrations/029, the
-- kinds 035, block and lot 036). A sidewalk has a block, a lot, an address and a property type; a ped ramp has an
-- intersection, a quadrant, the street it faces and a ramp type instead. Both have a borough.
------------------------------------------------------------
CREATE TYPE icid.property_kind AS ENUM ('sidewalk', 'ped_ramp');
CREATE TYPE icid.boro AS ENUM ('Manhattan', 'Bronx', 'Brooklyn', 'Queens', 'Staten Island');
CREATE TYPE icid.quadrant AS ENUM ('NE', 'NW', 'SE', 'SW');
CREATE TYPE icid.facing_street AS ENUM ('street_1', 'street_2');
CREATE TYPE icid.ramp_type AS ENUM ('PERPENDICULAR', 'PARALLEL', 'CUT_THROUGH');

------------------------------------------------------------
-- PED RAMP CORNER (one DOT pedestrian ramp inspection's corner: the form's inspection, location and construction
-- sections; soft-deleted; migrations/047, pedramp 2a). Its ramps are ped_ramp properties naming it (corner_id)
------------------------------------------------------------
CREATE TABLE icid.ped_ramp_corners (
    corner_id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id                    TEXT NOT NULL REFERENCES icid.projects(project_id),
    -- 1. Inspection information
    inspected_by                  TEXT,
    inspection_date               DATE,
    -- DOT's 13 entities (migration 048; one-way)
    entity                        TEXT CONSTRAINT ped_ramp_corners_entity_check CHECK (entity IN (
                                      'ddc_city_agency_capital', 'ddc_landmark_ped_ramp', 'ddc_dot_ped_ramp',
                                      'ddc_prior_notice', 'ddc_ta_ped_ramp', 'dep', 'dob', 'dpr', 'edc', 'mta',
                                      'nysdot', 'sca', 'other')),
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
    -- DOT's list (048; one-way: cobblestone dropped)
    curb_material                 TEXT CONSTRAINT ped_ramp_corners_curb_material_check CHECK (curb_material IN (
                                      'steel_faced', 'concrete', 'concrete_and_gutter', 'granite', 'bluestone',
                                      'other')),
    sidewalk_material             TEXT CHECK (sidewalk_material IN ('concrete', 'pigmented_concrete', 'bluestone',
                                                                    'cobblestone', 'other')),
    comments                      TEXT,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                    UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at                    TIMESTAMPTZ,
    deleted_by                    UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);
CREATE INDEX idx_ped_ramp_corners_project ON icid.ped_ramp_corners (project_id) WHERE deleted_at IS NULL;

CREATE TABLE icid.properties (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id             TEXT NOT NULL REFERENCES icid.projects(project_id),
    address                TEXT,
    property_type          TEXT,
    notes                  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by             UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at             TIMESTAMPTZ,
    deleted_by             UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    kind                   icid.property_kind NOT NULL DEFAULT 'sidewalk',
    boro                   icid.boro NOT NULL,
    violation_no           TEXT,
    intersection_street_1  TEXT,
    intersection_street_2  TEXT,
    quadrant               icid.quadrant,
    facing_street          icid.facing_street,
    ramp_type              icid.ramp_type,
    block                  TEXT,
    lot                    TEXT,
    -- Community Board, as DOT's sheets list it (Slice A, migration 043); optional for both kinds
    community_board        TEXT,
    CONSTRAINT chk_properties_type CHECK (property_type IN ('1_3_family', '4_plus_family', 'expedited')),
    -- A tax map number without its leading zeros: block at most 5 digits, lot at most 4
    CONSTRAINT chk_properties_block_format CHECK (block IS NULL OR block ~ '^(0|[1-9][0-9]{0,4})$'),
    CONSTRAINT chk_properties_lot_format CHECK (lot IS NULL OR lot ~ '^(0|[1-9][0-9]{0,3})$'),
    CONSTRAINT chk_properties_community_board_length CHECK (community_board IS NULL OR char_length(community_board) <= 10),
    -- The corner a ped ramp is at (migration 047); a sidewalk has none
    corner_id              UUID REFERENCES icid.ped_ramp_corners(corner_id),
    CONSTRAINT chk_properties_corner_kind CHECK (corner_id IS NULL OR kind = 'ped_ramp'),
    -- A sidewalk's tax-map frontage from DCP's MapPLUTO (Slice Y1, migration 058): all three set, or none
    tax_map_frontage_ft    NUMERIC(10,2),
    tax_map_source         TEXT,
    tax_map_fetched_at     TIMESTAMPTZ,
    CONSTRAINT chk_properties_tax_map_source
        CHECK (tax_map_source IS NULL OR tax_map_source ~ '^(mappluto_\d{2}v\d+(\.\d+)?|manual|pip_scrape|r1k)$'),
    CONSTRAINT chk_properties_tax_map_frontage
        CHECK (tax_map_frontage_ft IS NULL OR (tax_map_frontage_ft > 0 AND tax_map_frontage_ft < 1000)),
    CONSTRAINT chk_properties_tax_map_cache
        CHECK ((tax_map_frontage_ft IS NULL) = (tax_map_source IS NULL)
               AND (tax_map_source IS NULL) = (tax_map_fetched_at IS NULL)),
    -- A sidewalk's DOF Digital Tax Map PDF, its block's current sheet (Slice Y1.5, migration 059), in bucket tax-maps:
    -- fetched from DOF's map library (with DOF's map id and effective date) or uploaded by an admin
    tax_map_pdf_path            TEXT,
    tax_map_pdf_source          TEXT,
    tax_map_pdf_map_id          TEXT,
    tax_map_pdf_effective_date  DATE,
    tax_map_pdf_fetched_at      TIMESTAMPTZ,
    tax_map_pdf_uploaded_by     UUID REFERENCES icid.users(uuid),
    CONSTRAINT chk_properties_tax_map_pdf_source
        CHECK (tax_map_pdf_source IS NULL OR tax_map_pdf_source IN ('dof_map_library', 'upload')),
    CONSTRAINT chk_properties_tax_map_pdf
        CHECK ((tax_map_pdf_path IS NULL) = (tax_map_pdf_source IS NULL)
               AND (tax_map_pdf_source IS NULL) = (tax_map_pdf_fetched_at IS NULL)
               AND (tax_map_pdf_source IS DISTINCT FROM 'dof_map_library'
                    OR (tax_map_pdf_map_id IS NOT NULL AND tax_map_pdf_uploaded_by IS NULL))
               AND (tax_map_pdf_source IS DISTINCT FROM 'upload'
                    OR (tax_map_pdf_uploaded_by IS NOT NULL AND tax_map_pdf_map_id IS NULL
                        AND tax_map_pdf_effective_date IS NULL))
               AND (tax_map_pdf_source IS NOT NULL
                    OR (tax_map_pdf_map_id IS NULL AND tax_map_pdf_effective_date IS NULL
                        AND tax_map_pdf_uploaded_by IS NULL))),
    CONSTRAINT properties_kind_fields_consistent CHECK (
        (kind = 'sidewalk' AND
         block IS NOT NULL AND lot IS NOT NULL AND address IS NOT NULL AND property_type IS NOT NULL AND
         intersection_street_1 IS NULL AND intersection_street_2 IS NULL AND
         quadrant IS NULL AND facing_street IS NULL AND ramp_type IS NULL)
        OR
        (kind = 'ped_ramp' AND
         block IS NULL AND lot IS NULL AND address IS NULL AND property_type IS NULL AND violation_no IS NULL AND
         intersection_street_1 IS NOT NULL AND intersection_street_2 IS NOT NULL AND
         quadrant IS NOT NULL AND facing_street IS NOT NULL AND ramp_type IS NOT NULL)
    )
);

-- One live sidewalk per borough, block and lot in a project; a soft-deleted one frees it
CREATE UNIQUE INDEX uq_properties_project_sidewalk_bbl
    ON icid.properties (project_id, boro, block, lot) WHERE kind = 'sidewalk' AND deleted_at IS NULL;
CREATE INDEX idx_properties_corner ON icid.properties (corner_id) WHERE corner_id IS NOT NULL;

------------------------------------------------------------
-- PED RAMP ATTRIBUTES (one ped ramp property's measurements, the DOT form's section 5; migrations/047). Fields of
-- every ramp, then of its ramp type, all nullable; slopes in percent, lengths in inches. A cut-through's two ramps
-- are two properties, its length and DWS clearance kept on both
------------------------------------------------------------
CREATE TABLE icid.ped_ramp_attributes (
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
    -- DOT's lists (048; one-way: yellow, preformed_plastic, cast_in_place_plastic and the material's other dropped)
    dws_color                         TEXT CONSTRAINT ped_ramp_attributes_dws_color_check CHECK (dws_color IN (
                                          'red', 'white', 'dark_on_light', 'light_on_dark', 'other')),
    dws_material                      TEXT CONSTRAINT ped_ramp_attributes_dws_material_check CHECK (dws_material IN (
                                          'preformed_plastic_embedded', 'preformed_plastic_surface_applied',
                                          'cast_in_place_surface_applied', 'cast_iron', 'stone', 'concrete_tile')),
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
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The DOT form's "ramp location at corner" diagram position (048); blank for the pilot
    ramp_location_code                SMALLINT CONSTRAINT chk_ped_ramp_attributes_location_code
                                          CHECK (ramp_location_code BETWEEN 0 AND 22)
);

------------------------------------------------------------
-- PED RAMP INSPECTIONS (a corner's inspection: the review and signature wrapper around the corner and its ramps'
-- attributes; draft -> submitted (the inspector signs) -> approved (the RE signs), or returned to draft with a
-- comment; one live per corner; migrations/048)
------------------------------------------------------------
CREATE TABLE icid.ped_ramp_inspections (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corner_id                  UUID NOT NULL REFERENCES icid.ped_ramp_corners(corner_id),
    status                     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'approved')),
    requirements_met           BOOLEAN,
    inspector_uuid             UUID REFERENCES icid.users(uuid),
    submitted_at               TIMESTAMPTZ,
    inspector_signed_at        TIMESTAMPTZ,
    inspector_signature_path   TEXT,
    re_uuid                    UUID REFERENCES icid.users(uuid),
    re_signed_at               TIMESTAMPTZ,
    re_signature_path          TEXT,
    return_comment             TEXT,
    returned_at                TIMESTAMPTZ,
    returned_by                UUID REFERENCES icid.users(uuid),
    -- The DOT form filled at approval (pedramp-exports bucket)
    dot_pdf_path               TEXT,
    -- Its last admin unlock (migrations/051): when, and by which admin
    last_unlocked_at           TIMESTAMPTZ,
    last_unlocked_by           UUID REFERENCES icid.users(uuid),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                 UUID NOT NULL REFERENCES icid.users(uuid),
    CONSTRAINT chk_ped_ramp_inspections_submitted CHECK (status = 'draft' OR (
        inspector_uuid IS NOT NULL AND submitted_at IS NOT NULL AND inspector_signed_at IS NOT NULL
        AND inspector_signature_path IS NOT NULL)),
    CONSTRAINT chk_ped_ramp_inspections_approved CHECK (status <> 'approved' OR (
        re_uuid IS NOT NULL AND re_signed_at IS NOT NULL AND re_signature_path IS NOT NULL))
);
CREATE UNIQUE INDEX uq_ped_ramp_inspections_live_corner
    ON icid.ped_ramp_inspections (corner_id) WHERE status <> 'approved';
CREATE INDEX idx_ped_ramp_inspections_corner ON icid.ped_ramp_inspections (corner_id);

------------------------------------------------------------
-- PED RAMP TIFS (a Technical Infeasibility Form for one non-compliant ramp of an inspection; DOT form v1.9's
-- sections; drafted by an inspector or the RE, signed and approved by the RE; migrations/049)
------------------------------------------------------------
CREATE TABLE icid.ped_ramp_tifs (
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
    -- 2. Project information
    project_category                      TEXT CHECK (project_category IN ('city_agency', 'state_agency',
                                                                           'transit_authority', 'other')),
    project_category_name                 TEXT,
    dot_project_id                        TEXT,
    type_of_project                       TEXT,
    -- 3. Location information (the rest is the corner's)
    dot_corner_id                         TEXT,
    dot_ramp_id                           TEXT,
    ramp_construction                     TEXT CHECK (ramp_construction IN ('new_installation', 'upgrade')),
    -- 4. Why it is technically infeasible
    reason_limited_row                    BOOLEAN NOT NULL DEFAULT false,
    reason_existing_topography            BOOLEAN NOT NULL DEFAULT false,
    reason_existing_utilities             BOOLEAN NOT NULL DEFAULT false,
    reason_structures                     BOOLEAN NOT NULL DEFAULT false,
    reason_trees_natural                  BOOLEAN NOT NULL DEFAULT false,
    reason_nyct_structures                BOOLEAN NOT NULL DEFAULT false,
    reason_historic_features              BOOLEAN NOT NULL DEFAULT false,
    reason_other                          BOOLEAN NOT NULL DEFAULT false,
    reason_other_text                     TEXT,
    -- 5.
    why_requirements_cannot_be_achieved   TEXT,
    -- 6. Ramp elements that don't meet the minimum ADA standards
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
    -- 10. Statement and signature (the RE, at approval)
    signer_name                           TEXT,
    signer_ny_license_no                  TEXT,
    signer_signature_path                 TEXT,
    signed_at                             TIMESTAMPTZ,
    signed_by_uuid                        UUID REFERENCES icid.users(uuid),
    created_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                            UUID NOT NULL REFERENCES icid.users(uuid),
    CONSTRAINT chk_ped_ramp_tifs_signed CHECK (status <> 'approved' OR (
        signer_name IS NOT NULL AND signer_ny_license_no IS NOT NULL AND signer_signature_path IS NOT NULL
        AND signed_at IS NOT NULL AND signed_by_uuid IS NOT NULL))
);
CREATE UNIQUE INDEX uq_ped_ramp_tifs_ramp_inspection ON icid.ped_ramp_tifs (property_id, inspection_id);
CREATE INDEX idx_ped_ramp_tifs_inspection ON icid.ped_ramp_tifs (inspection_id);

------------------------------------------------------------
-- PED RAMP PHOTOS (an inspection's or a TIF's photos, in the pedramp-photos bucket; an inspection's name their ramp;
-- 'layout' is a TIF's section 9 image; at most 20 a parent, counted by the INSERT; migrations/050)
------------------------------------------------------------
CREATE TABLE icid.ped_ramp_photos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id   UUID REFERENCES icid.ped_ramp_inspections(id),
    tif_id          UUID REFERENCES icid.ped_ramp_tifs(id),
    property_id     UUID REFERENCES icid.properties(id),
    kind            TEXT NOT NULL DEFAULT 'photo' CHECK (kind IN ('photo', 'layout')),
    photo_number    INT NOT NULL CHECK (photo_number >= 1),
    caption         TEXT,
    storage_path    TEXT NOT NULL,
    content_type    TEXT,
    byte_size       INT CHECK (byte_size >= 0),
    uploaded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by     UUID NOT NULL REFERENCES icid.users(uuid),
    CONSTRAINT chk_ped_ramp_photos_one_parent CHECK ((inspection_id IS NULL) <> (tif_id IS NULL)),
    CONSTRAINT chk_ped_ramp_photos_inspection_ramp CHECK (inspection_id IS NULL OR property_id IS NOT NULL),
    CONSTRAINT chk_ped_ramp_photos_layout_on_tif CHECK (kind = 'photo' OR tif_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_ped_ramp_photos_inspection_number
    ON icid.ped_ramp_photos (inspection_id, photo_number) WHERE inspection_id IS NOT NULL;
CREATE UNIQUE INDEX uq_ped_ramp_photos_tif_number
    ON icid.ped_ramp_photos (tif_id, photo_number) WHERE tif_id IS NOT NULL AND kind = 'photo';
CREATE UNIQUE INDEX uq_ped_ramp_photos_tif_layout
    ON icid.ped_ramp_photos (tif_id) WHERE tif_id IS NOT NULL AND kind = 'layout';

------------------------------------------------------------
-- TASK ORDER (a project's task orders; soft-deleted like properties; migrations/030)
------------------------------------------------------------
CREATE TABLE icid.task_orders (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         TEXT NOT NULL REFERENCES icid.projects(project_id),
    task_order_number  TEXT NOT NULL,
    issued_date        DATE,
    estimate_notes     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at         TIMESTAMPTZ,
    deleted_by         UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);

-- One live task order per number in a project; a soft-deleted one frees its number
CREATE UNIQUE INDEX uq_task_orders_project_number
    ON icid.task_orders (project_id, task_order_number) WHERE deleted_at IS NULL;

------------------------------------------------------------
-- TASK ORDER PROPERTY (the bundle: which properties a task order holds; migrations/030)
-- Soft deletes on either side leave the rows alone; both sides share a project (checked when bundling)
------------------------------------------------------------
CREATE TABLE icid.task_order_properties (
    task_order_id  UUID NOT NULL REFERENCES icid.task_orders(id) ON DELETE CASCADE,
    property_id    UUID NOT NULL REFERENCES icid.properties(id) ON DELETE CASCADE,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by       UUID NOT NULL REFERENCES icid.users(uuid),
    PRIMARY KEY (task_order_id, property_id)
);

CREATE INDEX idx_task_order_properties_property ON icid.task_order_properties (property_id);

------------------------------------------------------------
-- PIR (a property's digitized DOT survey sheet; one live PIR per property; soft-deleted; migrations/031)
-- property_id has no cascade: a PIR is a record of its own. as_built_signed_* are set by a sign route, never by hand
------------------------------------------------------------
CREATE TYPE icid.compass_point AS ENUM ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW');

CREATE TABLE icid.pirs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id          UUID NOT NULL REFERENCES icid.properties(id),
    inspector_initials   TEXT NOT NULL,
    inspector_sign_date  DATE NOT NULL,
    digitized_by         UUID NOT NULL REFERENCES icid.users(uuid),
    digitized_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_built_signed_by   UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    as_built_signed_at   TIMESTAMPTZ,
    -- Which way North is on its sketch, every page alike (migrations/038); NULL: not known
    north_bearing        icid.compass_point,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    deleted_by           UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);

-- One live PIR per property; a soft-deleted one frees it for a replacement
CREATE UNIQUE INDEX uq_pirs_property ON icid.pirs (property_id) WHERE deleted_at IS NULL;

------------------------------------------------------------
-- PIR PAGE (one page of a PIR's scan, rendered to PNG in the pir-images bucket; migrations/033)
-- a re-upload or a delete replaces (soft-deletes) a PIR's live pages; their files are kept for flag history
------------------------------------------------------------
CREATE TABLE icid.pir_pages (
    pir_page_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_id           UUID NOT NULL REFERENCES icid.pirs(id) ON DELETE CASCADE,
    page_number      INT NOT NULL CHECK (page_number >= 1),
    image_path       TEXT NOT NULL,
    image_mime       TEXT NOT NULL,
    width_px         INT NOT NULL CHECK (width_px > 0),
    height_px        INT NOT NULL CHECK (height_px > 0),
    scale_px_per_ft  NUMERIC(10,4) CHECK (scale_px_per_ft IS NULL OR scale_px_per_ft > 0),
    rotation_deg     INT NOT NULL DEFAULT 0 CHECK (rotation_deg IN (0, 90, 180, 270)),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at       TIMESTAMPTZ,
    deleted_by       UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);

-- One live page per number in a PIR; replaced pages keep theirs
CREATE UNIQUE INDEX pir_pages_pir_page_num_live_uq ON icid.pir_pages (pir_id, page_number) WHERE deleted_at IS NULL;
CREATE INDEX pir_pages_pir_idx ON icid.pir_pages (pir_id);

------------------------------------------------------------
-- FLAG (a marked piece of sidewalk on a PIR: shape, survey classification, quantity; soft-deleted; migrations/032)
-- dimensions are checked per shape by the API; computed_quantity is the shape's area (sq ft), reported_quantity an
-- override (NULL: none). pay_item_id is SET NULL on delete: pay items are hard-deleted by the admin routes.
-- pir_page_id (migrations/033) is the page it is drawn on, sketch_x / sketch_y / rotation its place there; a live flag
-- has one (chk_flags_live_on_a_page), and the foreign key has no action: flags are never hard-deleted.
-- defect_codes (migrations/034) are the DOT defect codes marked on it; the non-assessable reasons are R1b's and the
-- R1e.1 survey's
------------------------------------------------------------
CREATE TABLE icid.flags (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_id                          UUID NOT NULL REFERENCES icid.pirs(id),
    pir_page_id                     UUID REFERENCES icid.pir_pages(pir_page_id),
    display_order                   INT NOT NULL,
    added_in_field                  BOOLEAN NOT NULL DEFAULT false,
    shape                           TEXT NOT NULL,
    dimensions                      JSONB NOT NULL,
    sketch_x                        REAL,
    sketch_y                        REAL,
    rotation                        REAL NOT NULL DEFAULT 0,
    surveyed_mark                   TEXT NOT NULL,
    surveyed_is_assessable          BOOLEAN NOT NULL,
    surveyed_non_assessable_reason  TEXT,
    surveyed_thickness              TEXT NOT NULL,
    is_constructed                  BOOLEAN NOT NULL DEFAULT false,
    pay_item_id                     UUID REFERENCES icid.contract_items(contract_item_id) ON DELETE SET NULL,
    computed_quantity               NUMERIC NOT NULL,
    reported_quantity               NUMERIC,
    quantity_delta_reason           TEXT,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by                      UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at                      TIMESTAMPTZ,
    deleted_by                      UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    defect_codes                    TEXT[] NOT NULL DEFAULT '{}',
    -- The approved IDR that built it, and that IDR's work date (migrations/037); no action: a claim is released
    -- (unlock, admin delete) before its IDR can be purged
    claimed_by_idr_id               UUID REFERENCES icid.idrs(idr_id),
    constructed_on                  DATE,
    CONSTRAINT chk_flags_display_order CHECK (display_order >= 0),
    CONSTRAINT chk_flags_shape CHECK (shape IN ('rectangle', 'trapezoid', 'triangle', 'polygon', 'arc')),
    CONSTRAINT chk_flags_surveyed_mark
        CHECK (surveyed_mark IN ('number_only', 'cross', 'cross_with_number', 'tick', 'none')),
    CONSTRAINT chk_flags_non_assessable_reason CHECK (surveyed_non_assessable_reason IS NULL OR
        surveyed_non_assessable_reason IN ('tree_damage', 'city_tree', 'transition', 'no_driveway', 'hydrant',
                                           'fronting_city', 'utility', 'settlement', 'other',
                                           'tree', 'driveway', 'grass', 'dirt', 'existing_sidewalk', 'asphalt',
                                           'tree_pit', 'landmark', 'vault', 'subway', 'scaffolding',
                                           'other_perm_obstruction', 'other_temp_obstruction',
                                           'outside_property_line')),
    CONSTRAINT chk_flags_thickness CHECK (surveyed_thickness IN ('4in', '7in', '7in_as_4in')),
    CONSTRAINT chk_flags_reported_quantity CHECK (reported_quantity IS NULL OR reported_quantity > 0),
    CONSTRAINT chk_flags_live_on_a_page CHECK (pir_page_id IS NOT NULL OR deleted_at IS NOT NULL),
    CONSTRAINT chk_flags_defect_codes
        CHECK (defect_codes <@ ARRAY['1', '2', '3', '4', '5', '6A', '6B', '6C']::TEXT[]),
    CONSTRAINT chk_flags_claim_consistent CHECK (
        (claimed_by_idr_id IS NULL AND is_constructed = false AND constructed_on IS NULL)
        OR (claimed_by_idr_id IS NOT NULL AND is_constructed = true AND constructed_on IS NOT NULL))
);

CREATE INDEX idx_flags_pir ON icid.flags (pir_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_flags_pay_item ON icid.flags (pay_item_id) WHERE deleted_at IS NULL AND pay_item_id IS NOT NULL;
CREATE INDEX flags_pir_page_idx ON icid.flags (pir_page_id);
CREATE INDEX idx_flags_claimed_by_idr ON icid.flags (claimed_by_idr_id) WHERE claimed_by_idr_id IS NOT NULL;
CREATE INDEX idx_flags_scope_rollup ON icid.flags (pay_item_id, claimed_by_idr_id) WHERE deleted_at IS NULL;

------------------------------------------------------------
-- FLAG EDITS (Slice Y1.5, migration 059): each change of a flag's surveyed_is_assessable or surveyed_non_assessable_reason,
-- by the sidewalk strip's auto-flip (editor NULL) or a flag edit; rows are only ever added
------------------------------------------------------------
CREATE TABLE icid.flag_edits (
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
CREATE INDEX idx_flag_edits_flag ON icid.flag_edits (flag_id, edited_at DESC);

------------------------------------------------------------
-- IDR FLAG ATTRIBUTION (an override of an IDR's default flag attribution; migrations/037)
-- exclude drops a flag the default holds, include adds one it doesn't
------------------------------------------------------------
CREATE TABLE icid.idr_flag_attributions (
    idr_id   UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    flag_id  UUID NOT NULL REFERENCES icid.flags(id) ON DELETE CASCADE,
    action   TEXT NOT NULL CHECK (action IN ('exclude', 'include')),
    set_by   UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    set_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (idr_id, flag_id)
);

CREATE INDEX idx_idr_flag_attributions_flag ON icid.idr_flag_attributions (flag_id);

------------------------------------------------------------
-- NON-ASSESSABLE AREA (a part of a flag that is not assessed: a driveway, grass, ...; soft-deleted; migrations/034)
-- computed_quantity is its shape's area (sq ft), taken off the flag's. Deleted with its flag (at the same moment) and
-- restored with it. The foreign key has no action: flags are never hard-deleted
------------------------------------------------------------
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
                                                'tree_pit', 'landmark', 'other', 'outside_property_line'))
);
CREATE INDEX nas_flag_idx ON icid.non_assessable_areas (flag_id);
CREATE UNIQUE INDEX nas_flag_order_live_uq ON icid.non_assessable_areas (flag_id, display_order)
    WHERE deleted_at IS NULL;

------------------------------------------------------------
-- FEATURE (a point obstruction or sized feature drawn on a page; soft-deleted; migrations/034)
-- A 'subtract' feature names the flag its area comes off (flag_id); computed_quantity is its shape's area (sq ft)
------------------------------------------------------------
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

------------------------------------------------------------
-- SEGMENT (a curb run drawn on a page; soft-deleted; migrations/034)
-- length_ft is worked out from points and the page's scale whenever either is written; NULL without a scale
------------------------------------------------------------
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
    CONSTRAINT chk_segments_length CHECK (length_ft IS NULL OR length_ft >= 0),
    -- A reported length overrides the drawn one for billing (migration 057); NULL means none
    reported_length_ft  NUMERIC,
    length_delta_reason TEXT,
    CONSTRAINT chk_segments_reported_length CHECK (reported_length_ft IS NULL OR reported_length_ft >= 0),
    -- Claimed by an approved IDR, and constructed on its report date (migration 044); cleared when it is unlocked or deleted
    claimed_by_idr_id UUID REFERENCES icid.idrs(idr_id),
    constructed_on    DATE,
    CONSTRAINT chk_segments_claim_consistent CHECK ((claimed_by_idr_id IS NULL) = (constructed_on IS NULL))
);
CREATE INDEX segments_page_idx ON icid.segments (pir_page_id);
CREATE INDEX segments_live_idx ON icid.segments (pir_page_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_segments_claimed_by_idr ON icid.segments (claimed_by_idr_id) WHERE claimed_by_idr_id IS NOT NULL;

------------------------------------------------------------
-- MATCH LINE (a lettered line joining a PIR's sheets, drawn on one page; soft-deleted; migrations/046, R1h.1a)
-- Two endpoints in the page's native pixels. Two pages pair by carrying the same label; a label is live on at most
-- one row per page (the index) and on at most two pages of a PIR (the API's statements)
------------------------------------------------------------
CREATE TABLE icid.pir_match_lines (
    match_line_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pir_page_id    UUID NOT NULL REFERENCES icid.pir_pages(pir_page_id),
    label          TEXT NOT NULL,
    x1             REAL NOT NULL,
    y1             REAL NOT NULL,
    x2             REAL NOT NULL,
    y2             REAL NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at     TIMESTAMPTZ,
    deleted_by     UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_match_lines_label CHECK (label ~ '^[A-Z]{1,2}$'),
    CONSTRAINT chk_match_lines_length CHECK (x1 <> x2 OR y1 <> y2)
);
CREATE UNIQUE INDEX pir_match_lines_page_label_live_uq
    ON icid.pir_match_lines (pir_page_id, label) WHERE deleted_at IS NULL;
CREATE INDEX pir_match_lines_page_idx ON icid.pir_match_lines (pir_page_id);

-- An IDR's overrides of its default segments: exclude one of its task order's sites', include one from elsewhere in
-- its project (migration 044), as idr_flag_attributions for flags
CREATE TABLE icid.idr_segment_attributions (
    idr_id      UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE,
    segment_id  UUID NOT NULL REFERENCES icid.segments(segment_id) ON DELETE CASCADE,
    action      TEXT NOT NULL CHECK (action IN ('exclude', 'include')),
    set_by      UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (idr_id, segment_id)
);
CREATE INDEX idx_idr_segment_attributions_segment ON icid.idr_segment_attributions (segment_id);

------------------------------------------------------------
-- PIR ANNOTATIONS (a page's title block, one row per page; migrations/034). No deleted_at: it stays with its page
------------------------------------------------------------
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
    -- The form's three dates, as typed (migrations/038). date_constructed is the pour, not flags.constructed_on
    date_notice_issued           DATE,
    date_excavated               DATE,
    date_constructed             DATE,
    -- Where tax_map_width_ft came from (Slice Y1, migration 058): mappluto_<version>, manual, ...; a width always has one
    tax_map_source               TEXT,
    tax_map_fetched_at           TIMESTAMPTZ,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_pir_annotations_tax_map_source
        CHECK (tax_map_source IS NULL OR tax_map_source ~ '^(mappluto_\d{2}v\d+(\.\d+)?|manual|pip_scrape|r1k)$'),
    CONSTRAINT chk_pir_annotations_tax_map_provenance
        CHECK ((tax_map_width_ft IS NULL OR tax_map_source IS NOT NULL)
               AND (tax_map_source IS NULL) = (tax_map_fetched_at IS NULL)),
    -- The page's sidewalk strip (Slice Y1.5, migration 059): the sidewalk in front of the lot as a closed polygon of
    -- {x, y} points in the page's native pixels, its first edge the property line; manual, or r1k (reserved)
    sidewalk_strip_points        JSONB,
    sidewalk_strip_source        TEXT,
    sidewalk_strip_set_at        TIMESTAMPTZ,
    sidewalk_strip_set_by        UUID REFERENCES icid.users(uuid),
    CONSTRAINT chk_pir_annotations_sidewalk_strip_points CHECK (sidewalk_strip_points IS NULL OR
        (jsonb_typeof(sidewalk_strip_points) = 'array' AND jsonb_array_length(sidewalk_strip_points) >= 3)),
    CONSTRAINT chk_pir_annotations_sidewalk_strip_source
        CHECK (sidewalk_strip_source IS NULL OR sidewalk_strip_source IN ('manual', 'r1k')),
    CONSTRAINT chk_pir_annotations_sidewalk_strip
        CHECK ((sidewalk_strip_points IS NULL) = (sidewalk_strip_source IS NULL)
               AND (sidewalk_strip_source IS NULL) = (sidewalk_strip_set_at IS NULL)
               AND (sidewalk_strip_set_by IS NULL OR sidewalk_strip_points IS NOT NULL)),
    CONSTRAINT chk_pir_annotations_boro
        CHECK (boro IS NULL OR boro IN ('Manhattan', 'Bronx', 'Brooklyn', 'Queens', 'Staten Island')),
    CONSTRAINT chk_pir_annotations_property_status CHECK (property_status IS NULL OR
        property_status IN ('all_defects_corrected', 'some_defects_corrected', 'no_work_done')),
    CONSTRAINT chk_pir_annotations_corner_status CHECK (corner_property_status IS NULL OR
        corner_property_status IN ('ped_ramp_installed', 'no_ped_ramp_exists', 'existing_ped_ramp')),
    CONSTRAINT chk_pir_annotations_widths CHECK ((tax_map_width_ft IS NULL OR tax_map_width_ft >= 0)
                                                 AND (cc_curb_to_curb_ft IS NULL OR cc_curb_to_curb_ft >= 0))
);

------------------------------------------------------------
-- REPORT ATTACHMENT (file metadata; bytes live in Supabase Storage)
------------------------------------------------------------
CREATE TABLE icid.report_attachments (
    attachment_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id        UUID NOT NULL REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE,
    file_name        TEXT NOT NULL,
    file_type        TEXT NOT NULL,
    file_size_bytes  INTEGER NOT NULL,
    storage_path     TEXT NOT NULL,
    uploaded_by      UUID NOT NULL REFERENCES icid.users(uuid),
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    attachment_name         TEXT    NOT NULL,
    attachment_description  TEXT,
    is_uploaded             BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT chk_report_attachments_size
        CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760),
    CONSTRAINT chk_report_attachments_name
        CHECK (btrim(attachment_name) <> '' AND char_length(attachment_name) <= 200),
    CONSTRAINT chk_report_attachments_description
        CHECK (attachment_description IS NULL
            OR (btrim(attachment_description) <> '' AND char_length(attachment_description) <= 2000))
);

CREATE INDEX idx_report_attachments_report_id ON icid.report_attachments(report_id);

------------------------------------------------------------
-- SPEC ITEM (shared NYCDOT pay-item catalog; migrations/011)
------------------------------------------------------------
CREATE TABLE icid.spec_items (
    spec_item_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_no        TEXT NOT NULL,
    description    TEXT NOT NULL,
    spec_section   TEXT NOT NULL,
    pay_unit       TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_spec_items_item_no UNIQUE (item_no)
);

------------------------------------------------------------
-- CONTRACT ITEM (a project's Schedule of Bid Items; migrations/011, 026)
-- A row carries its own item number, description and unit. spec_item_id
-- is set only on rows that came from the spec_items catalog.
------------------------------------------------------------
CREATE TABLE icid.contract_items (
    contract_item_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id        TEXT NOT NULL REFERENCES icid.projects(project_id) ON DELETE CASCADE,
    spec_item_id      UUID REFERENCES icid.spec_items(spec_item_id),
    budget_code       TEXT NOT NULL,
    bid_quantity      NUMERIC NOT NULL,
    bid_unit_price    NUMERIC NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    item_no           TEXT NOT NULL,
    description       TEXT NOT NULL,
    unit              TEXT NOT NULL,
    spec_section      TEXT,
    CONSTRAINT uq_contract_items_project_item_budget UNIQUE (project_id, item_no, budget_code)
);

CREATE INDEX idx_contract_items_project ON icid.contract_items(project_id);
CREATE INDEX idx_contract_items_spec_item ON icid.contract_items(spec_item_id);

------------------------------------------------------------
-- DEMO MODE: daily backstop for demo users who never signed out
-- (migrations/014_demo_cleanup.sql; scheduling options are listed there)
------------------------------------------------------------
CREATE OR REPLACE FUNCTION icid.cleanup_abandoned_demo_users()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = icid, pg_temp
AS $$
DECLARE
    stale UUID[];
    purged_count INTEGER;
BEGIN
    SELECT array_agg(u.uuid) INTO stale
    FROM icid.users u
    WHERE u.is_demo = true AND u.created_at < now() - interval '24 hours';

    IF stale IS NULL THEN
        RETURN 0;
    END IF;

    DELETE FROM icid.report_attachments a
    WHERE a.uploaded_by = ANY(stale)
       OR a.report_id IN (
           SELECT r.report_id
           FROM icid.idr_reports r
           JOIN icid.idrs i ON i.idr_id = r.idr_id
           WHERE i.reporter_uuid = ANY(stale)
       );

    DELETE FROM icid.idr_reports r
    WHERE r.idr_id IN (SELECT i.idr_id FROM icid.idrs i WHERE i.reporter_uuid = ANY(stale));

    DELETE FROM icid.idrs i WHERE i.reporter_uuid = ANY(stale);

    DELETE FROM icid.project_users pu WHERE pu.user_uuid = ANY(stale);

    DELETE FROM icid.users u WHERE u.uuid = ANY(stale) AND u.is_demo = true;
    GET DIAGNOSTICS purged_count = ROW_COUNT;

    RETURN purged_count;
END;
$$;

REVOKE ALL ON FUNCTION icid.cleanup_abandoned_demo_users() FROM PUBLIC;
