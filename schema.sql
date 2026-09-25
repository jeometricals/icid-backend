-- schema.sql
-- Authoritative DDL for the icid schema.
--
-- Data model: IDR (Inspector Daily Diary). One icid.idrs row per inspector
-- per project per day holds the shared header; icid.idr_reports holds the
-- typed reports (General, addenda, ...) filed under it. Introduced in
-- migrations/004_idr_refactor.sql (Phase R, Slice R1).
--
-- LEGACY: icid.reports and icid.completed_forms are the pre-IDR model. Their
-- data was migrated into idrs/idr_reports; the tables are kept until the
-- backend and frontend move over, and are dropped in Slice R5.
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
    CONSTRAINT fk_users_client
        FOREIGN KEY (client_id) REFERENCES icid.clients(client_id)
);

CREATE INDEX idx_users_uuid ON icid.users(uuid);
CREATE INDEX idx_users_email ON icid.users(email);

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
-- REPORT (LEGACY - superseded by idrs/idr_reports, dropped in Slice R5)
------------------------------------------------------------
CREATE TABLE icid.reports (
    report_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    reporter_uuid      UUID NOT NULL,
    project_id         TEXT NOT NULL,
    report_date        DATE,
    status             TEXT NOT NULL DEFAULT 'draft',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at       TIMESTAMPTZ,
    CONSTRAINT chk_reports_status
        CHECK (status IN ('draft', 'submitted')),
    CONSTRAINT fk_reports_project
        FOREIGN KEY (project_id) REFERENCES icid.projects(project_id),
    CONSTRAINT fk_reports_reporter
        FOREIGN KEY (reporter_uuid) REFERENCES icid.users(uuid)
);

CREATE INDEX idx_reports_project_id ON icid.reports(project_id);
CREATE INDEX idx_reports_date ON icid.reports(report_date);

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
-- COMPLETED FORM (LEGACY - superseded by idr_reports, dropped in Slice R5)
------------------------------------------------------------
CREATE TABLE icid.completed_forms (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    completed_form_id  TEXT UNIQUE NOT NULL DEFAULT uuid_generate_v4()::text,
    report_id          UUID NOT NULL,
    form_template_id   TEXT NOT NULL,
    form_data          JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_completed_forms_report
        FOREIGN KEY (report_id) REFERENCES icid.reports(report_id),
    CONSTRAINT fk_completed_forms_template
        FOREIGN KEY (form_template_id) REFERENCES icid.form_templates(form_template_id),
    CONSTRAINT uq_completed_forms_report_template
        UNIQUE (report_id, form_template_id)
);

CREATE INDEX idx_completed_forms_report ON icid.completed_forms(report_id);
CREATE INDEX idx_completed_forms_template ON icid.completed_forms(form_template_id);

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
