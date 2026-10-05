-- seed_test_project.sql
-- The Test Project (Slice H0): a minimal project, DEMO01, with only its required fields.
--
-- Idempotent. Run after schema.sql:
--   psql -d icid -f seed_test_project.sql

BEGIN;

INSERT INTO icid.projects (project_id, project_name, project_description, registration_code, borough, status)
VALUES ('DEMO01', 'Test Project', NULL, NULL, NULL, NULL)
ON CONFLICT (project_id) DO NOTHING;

COMMIT;
