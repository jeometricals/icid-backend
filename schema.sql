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
    CONSTRAINT fk_users_client
        FOREIGN KEY (client_id) REFERENCES icid.clients(client_id),
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE INDEX idx_users_uuid ON icid.users(uuid);
CREATE INDEX idx_users_email ON icid.users(email);
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
    PRIMARY KEY (project_id, user_uuid),
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
    CONSTRAINT chk_idrs_status CHECK (status IN ('draft', 'submitted')),
    CONSTRAINT uq_idrs_project_reporter_date UNIQUE (project_id, reporter_uuid, report_date)
);

CREATE INDEX idx_idrs_project_status ON icid.idrs(project_id, status);
CREATE INDEX idx_idrs_reporter ON icid.idrs(reporter_uuid);

------------------------------------------------------------
-- IDR REPORT (typed report within an IDR; addenda link to a parent)
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
-- CONTRACT ITEM (a project's Schedule of Bid Items; migrations/011)
------------------------------------------------------------
CREATE TABLE icid.contract_items (
    contract_item_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id        TEXT NOT NULL REFERENCES icid.projects(project_id) ON DELETE CASCADE,
    spec_item_id      UUID NOT NULL REFERENCES icid.spec_items(spec_item_id),
    budget_code       TEXT NOT NULL,
    bid_quantity      NUMERIC(12,2) NOT NULL,
    bid_unit_price    NUMERIC(12,2) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_contract_items_project_spec_budget UNIQUE (project_id, spec_item_id, budget_code)
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
