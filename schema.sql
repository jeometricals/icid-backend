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
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
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
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_quantities_project_date ON icid.quantities(project_id, report_date);
CREATE INDEX idx_quantities_project_item ON icid.quantities(project_id, pay_item_ref);
CREATE INDEX idx_quantities_project_budget ON icid.quantities(project_id, budget_code);
CREATE INDEX idx_quantities_idr ON icid.quantities(idr_id);

------------------------------------------------------------
-- PROPERTY (a project's tax lots, by BBL; soft-deleted like users and idrs; migrations/029)
------------------------------------------------------------
CREATE TABLE icid.properties (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     TEXT NOT NULL REFERENCES icid.projects(project_id),
    bbl            TEXT NOT NULL,
    address        TEXT NOT NULL,
    property_type  TEXT NOT NULL,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     UUID NOT NULL REFERENCES icid.users(uuid),
    deleted_at     TIMESTAMPTZ,
    deleted_by     UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    CONSTRAINT chk_properties_type CHECK (property_type IN ('1_3_family', '4_plus_family', 'expedited'))
);

-- One live property per BBL in a project; a soft-deleted one frees its BBL
CREATE UNIQUE INDEX uq_properties_project_bbl ON icid.properties (project_id, bbl) WHERE deleted_at IS NULL;

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
CREATE TABLE icid.pirs (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id          UUID NOT NULL REFERENCES icid.properties(id),
    source_image_url     TEXT,
    source_image_scale   REAL,
    inspector_initials   TEXT NOT NULL,
    inspector_sign_date  DATE NOT NULL,
    digitized_by         UUID NOT NULL REFERENCES icid.users(uuid),
    digitized_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_built_signed_by   UUID REFERENCES icid.users(uuid) ON DELETE SET NULL,
    as_built_signed_at   TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    deleted_by           UUID REFERENCES icid.users(uuid) ON DELETE SET NULL
);

-- One live PIR per property; a soft-deleted one frees it for a replacement
CREATE UNIQUE INDEX uq_pirs_property ON icid.pirs (property_id) WHERE deleted_at IS NULL;

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
    attachment_description  TEXT    NOT NULL,
    is_uploaded             BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT chk_report_attachments_size
        CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760),
    CONSTRAINT chk_report_attachments_name
        CHECK (btrim(attachment_name) <> '' AND char_length(attachment_name) <= 200),
    CONSTRAINT chk_report_attachments_description
        CHECK (btrim(attachment_description) <> '' AND char_length(attachment_description) <= 2000)
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
