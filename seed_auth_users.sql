-- seed_auth_users.sql
-- Auth users (Slice H0): Reza's admin account.
--
-- Reza's account carries the bcrypt hash of a placeholder password, to be rotated once
-- sign-in lands. Re-running never resets a password that is already set. It sits under
-- client C00001 (users.client_id is NOT NULL).
--
-- Reza is also assigned to every seeded project that exists (HWS0023, SE384, DEMO01): the admin
-- role doesn't widen what a user sees, so without an assignment he would list no projects.
--
-- It also puts back Genghis Khan's email. He is seed.sql's first user, and in the live
-- database his uuid is 327d3ed2-a3d6-4235-9408-7fe721b12bed, the id the frontend sent for
-- everyone before sign-in existed. An earlier version of this file took that row for a
-- placeholder "legacy demo user" and renamed it legacy-demo@icid.local. The UPDATE below
-- undoes that where it happened and does nothing anywhere else (a database built from
-- seed.sql has Genghis under another uuid, with his email intact).
--
-- Idempotent. Run after schema.sql (or migrations/016, for the case-insensitive email index
-- the upsert relies on, and migrations/018, for the project_users key the assignment's
-- ON CONFLICT names), seed.sql (which creates C00001) and seed_test_project.sql (so DEMO01
-- is assigned too):
--   psql -d icid -f seed_auth_users.sql

BEGIN;

INSERT INTO icid.users (email, first_name, client_id, password_hash, role, is_demo)
VALUES ('reza@icid.local', 'Reza', 'C00001',
        '$2b$12$t4tLFWw0/EVvuNYznot6Ju7YXu9xnWR7F8E948wG6YGAyb9v02zu.', 'admin', false)
ON CONFLICT (lower(email)) DO UPDATE
SET role = EXCLUDED.role,
    is_demo = EXCLUDED.is_demo,
    password_hash = COALESCE(icid.users.password_hash, EXCLUDED.password_hash);

UPDATE icid.users
SET email = 'KhanG@magnoleng.pc'
WHERE uuid = '327d3ed2-a3d6-4235-9408-7fe721b12bed'
  AND email = 'legacy-demo@icid.local';

INSERT INTO icid.project_users (project_id, user_uuid, user_role)
SELECT p.project_id, u.uuid, 'Admin'
FROM icid.users u
JOIN icid.projects p ON p.project_id IN ('HWS0023', 'SE384', 'DEMO01')
WHERE u.email = 'reza@icid.local'
ON CONFLICT (project_id, user_uuid, role) DO NOTHING;

COMMIT;
