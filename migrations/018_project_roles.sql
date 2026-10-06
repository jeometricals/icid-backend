-- migrations/018_project_roles.sql
-- Project roles (Slice J0, 2026-10-06): what a user may do on a project, one row per role.
--
--   icid.project_users
--     + role  TEXT NOT NULL DEFAULT 'inspector'   -- 'inspector', 'oe' or 're' (chk_project_users_role)
--     ~ primary key  (project_id, user_uuid)  ->  (project_id, user_uuid, role)
--
-- Roles are additive: a user holding several roles on a project has one row per role, which
-- the old primary key (one row per user per project) did not allow. Every existing row
-- becomes an 'inspector' row. Admin is not a project role: it stays on icid.users.role.
--
-- user_role (free text: 'Inspector', 'CCL', 'Demo', ...) is left as it is. It is a label the
-- projects list shows; role is what the API checks.
--
-- seed_auth_users.sql upserts ON CONFLICT (project_id, user_uuid, role), so it needs this
-- migration first.
--
-- Idempotent: the column is added IF NOT EXISTS (its CHECK comes with it), and the primary
-- key is replaced only while it is still the two-column one. Run in the Supabase SQL editor.
-- Everything between BEGIN and COMMIT is one transaction: if any statement fails, nothing
-- is applied.

-- ============================================================
-- PRE-CHECKS (run individually before the migration)
-- ============================================================

-- 1. The role column does not exist yet:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'project_users' AND column_name = 'role';
--   Expected: 0 rows

-- 2. The primary key is the baseline's, on (project_id, user_uuid):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.project_users'::regclass AND contype = 'p';
--   Expected: 1 row, PRIMARY KEY (project_id, user_uuid)

-- 3. Nothing else depends on that primary key (a foreign key pointing at project_users
--    would block dropping it):
--   SELECT conname, conrelid::regclass FROM pg_constraint
--   WHERE confrelid = 'icid.project_users'::regclass;
--   Expected: 0 rows

-- 4. How many assignments there are (they all become 'inspector'):
--   SELECT count(*) FROM icid.project_users;

-- ============================================================
-- MIGRATION
-- ============================================================

BEGIN;

ALTER TABLE icid.project_users
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'inspector'
        CONSTRAINT chk_project_users_role CHECK (role IN ('inspector', 'oe', 're'));

DO $$
DECLARE
    pk_name    TEXT;
    pk_columns INTEGER;
BEGIN
    SELECT conname, cardinality(conkey) INTO pk_name, pk_columns
    FROM pg_constraint
    WHERE conrelid = 'icid.project_users'::regclass AND contype = 'p';

    IF pk_name IS NOT NULL AND pk_columns = 3 THEN
        RETURN;
    END IF;
    IF pk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE icid.project_users DROP CONSTRAINT %I', pk_name);
    END IF;
    ALTER TABLE icid.project_users
        ADD CONSTRAINT project_users_pkey PRIMARY KEY (project_id, user_uuid, role);
END $$;

COMMIT;

-- ============================================================
-- VERIFICATION (run after the migration)
-- ============================================================

-- A. The column:
--   SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
--   WHERE table_schema = 'icid' AND table_name = 'project_users' AND column_name = 'role';
--   Expected: role text NO 'inspector'::text

-- B. The CHECK and the primary key:
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--   WHERE conrelid = 'icid.project_users'::regclass AND contype IN ('c', 'p') ORDER BY conname;
--   Expected: chk_project_users_role, CHECK ((role = ANY (ARRAY['inspector'::text, 'oe'::text, 're'::text])))
--             project_users_pkey, PRIMARY KEY (project_id, user_uuid, role)

-- C. Every existing assignment is an inspector's, and none was lost:
--   SELECT role, count(*) FROM icid.project_users GROUP BY role;
--   Expected: 1 row, 'inspector', the count from pre-check 4
