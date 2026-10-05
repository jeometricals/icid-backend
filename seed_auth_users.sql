-- seed_auth_users.sql
-- Auth users (Slice H0): Reza's admin account and the legacy demo user.
--
-- Reza's account carries the bcrypt hash of a placeholder password, to be rotated once
-- sign-in lands. Re-running never resets a password that is already set.
--
-- The legacy demo user keeps its hardcoded uuid so callers that still send it keep
-- working until sign-in replaces them. It has no password and no role, and is not a
-- demo account (is_demo = false), so the demo cleanup leaves it alone. If the row
-- already exists, only its email, role and is_demo change.
--
-- Both sit under client C00001 (users.client_id is NOT NULL).
--
-- Idempotent. Run after schema.sql (or migrations/013) and seed.sql, which creates C00001:
--   psql -d icid -f seed_auth_users.sql

BEGIN;

INSERT INTO icid.users (email, first_name, client_id, password_hash, role, is_demo)
VALUES ('reza@icid.local', 'Reza', 'C00001',
        '$2b$12$t4tLFWw0/EVvuNYznot6Ju7YXu9xnWR7F8E948wG6YGAyb9v02zu.', 'admin', false)
ON CONFLICT (email) DO UPDATE
SET role = EXCLUDED.role,
    is_demo = EXCLUDED.is_demo,
    password_hash = COALESCE(icid.users.password_hash, EXCLUDED.password_hash);

INSERT INTO icid.users (uuid, email, client_id, role, is_demo)
VALUES ('327d3ed2-a3d6-4235-9408-7fe721b12bed', 'legacy-demo@icid.local', 'C00001', NULL, false)
ON CONFLICT (uuid) DO UPDATE
SET email = EXCLUDED.email,
    role = EXCLUDED.role,
    is_demo = EXCLUDED.is_demo;

COMMIT;
