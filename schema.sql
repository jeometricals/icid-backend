-- schema.sql
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
-- REPORT
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
-- COMPLETED FORM
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
