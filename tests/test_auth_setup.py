"""
Auth groundwork (H0): the users schema, its migration and seeds, read as text (no database), and the JWT settings.
"""

import importlib
import re
from pathlib import Path

import bcrypt
import pytest

from api.core import config

ROOT = Path(__file__).resolve().parents[1]
DEMO_UUID = "327d3ed2-a3d6-4235-9408-7fe721b12bed"
IS_DEMO_INDEX = "idx_users_is_demo ON icid.users(is_demo) WHERE is_demo = true;"


def sql(name: str) -> str:
    """
    Read a SQL file without its comments.
    Takes the file's path under the repo root.
    Returns its statements as text.
    """
    return re.sub(r"--[^\n]*", "", (ROOT / name).read_text(encoding="utf-8"))


def table_columns(table: str) -> dict[str, str]:
    """
    Read a table's columns from schema.sql.
    Takes the table's name within the icid schema.
    Returns {column name: the rest of its definition}, constraints left out.
    """
    body = re.search(rf"CREATE TABLE icid\.{table} \((.*?)\n\);", sql("schema.sql"), re.DOTALL).group(1)
    lines = [line.strip().rstrip(",") for line in body.splitlines() if line.strip()]
    return {name: rest.strip() for name, rest in (line.split(None, 1) for line in lines
                                                  if not line.startswith(("CONSTRAINT", "FOREIGN", "PRIMARY")))}


def inserted_columns(script: str, table: str) -> list[list[str]]:
    """
    Read the column lists of a seed script's INSERTs into one table.
    Takes the script's text and the table's name within the icid schema.
    Returns one list of column names per INSERT.
    """
    return [re.split(r",\s*", columns) for columns in re.findall(rf"INSERT INTO icid\.{table} \(([^)]+)\)", script)]


@pytest.fixture
def reload_config(monkeypatch):
    """
    Re-import api.core.config under a changed environment, ignoring any .env file, and restore it afterwards.
    Yields a function taking JWT_* values (None unsets one) and returning the reloaded module.
    """
    def reload(**env):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
        for name, value in env.items():
            monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(name, value)
        return importlib.reload(config)

    yield reload
    monkeypatch.undo()
    importlib.reload(config)


class TestUsersSchema:
    def test_users_has_the_auth_columns(self):
        columns = table_columns("users")
        assert columns["uuid"].startswith("UUID PRIMARY KEY")
        assert columns["email"] == "TEXT NOT NULL"
        assert (columns["password_hash"], columns["role"]) == ("TEXT", "TEXT")  # both nullable
        assert columns["is_demo"] == "BOOLEAN NOT NULL DEFAULT false"
        assert columns["created_at"].startswith("TIMESTAMPTZ NOT NULL")  # the demo cleanup reads it
        assert "name" not in columns and {"first_name", "last_name"} <= set(columns)

    def test_email_is_unique_and_demo_users_are_indexed(self):
        schema = sql("schema.sql")
        assert "CREATE UNIQUE INDEX idx_users_email_lower ON icid.users (lower(email));" in schema
        assert "uq_users_email" not in schema and "idx_users_email ON" not in schema  # replaced by migration 016
        assert f"CREATE INDEX {IS_DEMO_INDEX}" in schema


class TestMigration013:
    def test_it_adds_what_schema_sql_declares(self):
        migration = sql("migrations/013_auth_users.sql")
        added = dict(re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)\s+([^,;]+)", migration))
        columns = table_columns("users")
        assert added == {name: columns[name] for name in ("password_hash", "role", "is_demo")}
        assert "ALTER TABLE icid.users ADD CONSTRAINT uq_users_email UNIQUE (email);" in migration
        assert f"CREATE INDEX IF NOT EXISTS {IS_DEMO_INDEX}" in migration

    def test_it_is_one_transaction_and_safe_to_rerun(self):
        migration = sql("migrations/013_auth_users.sql")
        assert migration.index("BEGIN;") < migration.index("ALTER TABLE") < migration.rindex("COMMIT;")
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        # A second run finds the constraint and skips it
        assert "IF NOT EXISTS (\n        SELECT 1 FROM pg_constraint\n        WHERE conname = 'uq_users_email'" in migration
        assert "NOT NULL" not in dict(re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)\s+([^,;]+)", migration))[
            "password_hash"]  # existing users have no password


class TestSeeds:
    def test_auth_users_seeds_reza_as_admin_with_a_bcrypt_hash(self):
        seed = sql("seed_auth_users.sql")
        row = re.search(r"VALUES \('reza@icid\.local', 'Reza', 'C00001',\s+'([^']+)', 'admin', false\)", seed)
        assert re.fullmatch(r"\$2b\$12\$[./A-Za-z0-9]{53}", row.group(1))
        assert bcrypt.checkpw(b"not the password", row.group(1).encode()) is False  # a hash bcrypt can read
        assert "password_hash = COALESCE(icid.users.password_hash, EXCLUDED.password_hash)" in seed

    def test_auth_users_upserts_on_the_case_insensitive_email(self):
        seed = sql("seed_auth_users.sql")
        assert "ON CONFLICT (lower(email)) DO UPDATE" in seed  # the only unique index on email since 016
        assert "ON CONFLICT (email)" not in seed

    def test_auth_users_puts_genghis_khans_email_back_and_nothing_else(self):
        seed = re.sub(r"\s+", " ", sql("seed_auth_users.sql"))
        assert (f"UPDATE icid.users SET email = 'KhanG@magnoleng.pc' WHERE uuid = '{DEMO_UUID}' "
                "AND email = 'legacy-demo@icid.local';") in seed
        # no row is created for that uuid any more, so a database built from seed.sql gets no second Genghis
        assert inserted_columns(sql("seed_auth_users.sql"), "users") == [
            ["email", "first_name", "client_id", "password_hash", "role", "is_demo"]]
        assert seed.count("legacy-demo@icid.local") == 1
        assert "'KhanG@magnoleng.pc'" in sql("seed.sql")  # the email seed.sql gives him

    def test_auth_users_assigns_reza_to_the_seeded_projects(self):
        seed = sql("seed_auth_users.sql")
        assert inserted_columns(seed, "project_users") == [["project_id", "user_uuid", "user_role"]]
        assert "JOIN icid.projects p ON p.project_id IN ('HWS0023', 'SE384', 'DEMO01')" in seed
        assert "WHERE u.email = 'reza@icid.local'" in seed
        assert "ON CONFLICT (project_id, user_uuid, role) DO NOTHING" in seed  # the primary key since 018
        assert set(table_columns("project_users")) >= {"project_id", "user_uuid", "user_role"}

    def test_test_project_seeds_demo01(self):
        seed = sql("seed_test_project.sql")
        assert "VALUES ('DEMO01', 'Test Project', NULL, NULL, NULL, NULL)" in seed
        assert "ON CONFLICT (project_id) DO NOTHING" in seed

    def test_the_seeds_name_only_real_columns_and_fill_the_required_ones(self):
        for script, table in (("seed_auth_users.sql", "users"), ("seed_test_project.sql", "projects")):
            columns = table_columns(table)
            required = {name for name, rest in columns.items() if "NOT NULL" in rest and "DEFAULT" not in rest}
            inserts = inserted_columns(sql(script), table)
            assert inserts, script
            for named in inserts:
                assert set(named) <= set(columns), (script, named)
                assert required <= set(named), (script, named)


class TestJwtConfig:
    def test_the_app_wont_start_without_a_secret_key(self, reload_config):
        with pytest.raises(ValueError, match="JWT_SECRET_KEY is not set"):
            reload_config(JWT_SECRET_KEY=None)
        with pytest.raises(ValueError, match="JWT_SECRET_KEY is not set"):
            reload_config(JWT_SECRET_KEY="")

    def test_expiry_and_algorithm_have_defaults(self, reload_config):
        settings = reload_config(JWT_SECRET_KEY="a-key", JWT_EXPIRY_SECONDS=None, JWT_ALGORITHM=None)
        assert (settings.JWT_SECRET_KEY, settings.JWT_EXPIRY_SECONDS, settings.JWT_ALGORITHM) == (
            "a-key", 86400, "HS256")

    def test_expiry_and_algorithm_can_be_set(self, reload_config):
        settings = reload_config(JWT_SECRET_KEY="a-key", JWT_EXPIRY_SECONDS="3600", JWT_ALGORITHM="HS512")
        assert (settings.JWT_EXPIRY_SECONDS, settings.JWT_ALGORITHM) == (3600, "HS512")


def test_the_auth_libraries_are_pinned():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "PyJWT==2.15.0" in requirements and "bcrypt==5.0.0" in requirements


class TestDemoCleanupMigration:
    def function_sql(self, name: str) -> str:
        """
        Cut the cleanup function (through its REVOKE) out of a SQL file.
        Takes the file's path under the repo root.
        Returns the function's text.
        """
        text = (ROOT / name).read_text(encoding="utf-8")
        start = text.index("CREATE OR REPLACE FUNCTION icid.cleanup_abandoned_demo_users()")
        end = text.index("FROM PUBLIC;", start) + len("FROM PUBLIC;")
        return text[start:end]

    def test_schema_sql_carries_the_same_function(self):
        assert self.function_sql("migrations/014_demo_cleanup.sql") == self.function_sql("schema.sql")

    def test_the_function_is_well_formed(self):
        function = self.function_sql("migrations/014_demo_cleanup.sql")
        assert "RETURNS INTEGER" in function and "LANGUAGE plpgsql" in function
        assert "SECURITY DEFINER" in function and "SET search_path = icid, pg_temp" in function
        assert function.count("$$") == 2 and function.count("(") == function.count(")")
        body = function.split("$$")[1]
        assert body.strip().startswith("DECLARE") and body.strip().endswith("END;")
        assert body.count("BEGIN") == 1 and body.count("IF stale IS NULL THEN") == body.count("END IF;") == 1
        assert "RETURN purged_count;" in body and "GET DIAGNOSTICS purged_count = ROW_COUNT;" in body
        statements = [s for s in body.split(";") if "DELETE FROM" in s]
        assert len(statements) == 5
        assert function.rstrip().endswith("REVOKE ALL ON FUNCTION icid.cleanup_abandoned_demo_users() FROM PUBLIC;")

    def test_it_purges_only_demo_users_older_than_a_day_children_first(self):
        function = self.function_sql("migrations/014_demo_cleanup.sql")
        assert "WHERE u.is_demo = true AND u.created_at < now() - interval '24 hours'" in function
        assert re.findall(r"DELETE FROM icid\.(\w+)", function) == [
            "report_attachments", "idr_reports", "idrs", "project_users", "users"]
        assert "DELETE FROM icid.users u WHERE u.uuid = ANY(stale) AND u.is_demo = true;" in function
        assert function.count("ANY(stale)") == 6  # every delete is limited to the stale demo users

    def test_the_migration_is_one_transaction_and_lists_the_scheduling_options(self):
        text = (ROOT / "migrations/014_demo_cleanup.sql").read_text(encoding="utf-8")
        migration = sql("migrations/014_demo_cleanup.sql")
        assert migration.index("BEGIN;") < migration.index("CREATE OR REPLACE FUNCTION") < migration.rindex("COMMIT;")
        assert "ALTER TABLE" not in migration and "CREATE TABLE" not in migration
        for option in ("cron.schedule('cleanup-abandoned-demos', '0 3 * * *'", "Vercel Cron",
                       "SELECT icid.cleanup_abandoned_demo_users();"):
            assert option in text


class TestMigration016:
    def test_email_becomes_unique_without_regard_to_case(self):
        migration = sql("migrations/016_users_email_lower.sql")
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower ON icid.users (lower(email));" in migration
        assert "ALTER TABLE icid.users DROP CONSTRAINT IF EXISTS uq_users_email;" in migration
        assert "DROP INDEX IF EXISTS icid.idx_users_email;" in migration

    def test_the_new_index_is_built_before_the_old_guarantee_goes(self):
        migration = sql("migrations/016_users_email_lower.sql")
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        order = [migration.index(part) for part in ("BEGIN;", "CREATE UNIQUE INDEX", "DROP CONSTRAINT", "DROP INDEX",
                                                    "COMMIT;")]
        assert order == sorted(order)

    def test_it_carries_the_duplicate_pre_check(self):
        text = (ROOT / "migrations" / "016_users_email_lower.sql").read_text(encoding="utf-8")
        assert "SELECT lower(email), count(*) FROM icid.users GROUP BY lower(email) HAVING count(*) > 1;" in text

    def test_it_matches_schema_sql_and_what_sign_in_asks(self):
        from unittest.mock import patch
        from api.queries.users import get_user_for_auth
        assert "ON icid.users (lower(email));" in sql("schema.sql")
        with patch("api.queries.users.run_query", return_value=[]) as run:
            get_user_for_auth("Reza@ICID.local")
        assert "WHERE lower(u.email) = %s" in run.call_args.args[0]  # the expression the index is on
        assert run.call_args.args[1] == ("reza@icid.local",)

    def test_no_data_is_touched(self):
        migration = sql("migrations/016_users_email_lower.sql")
        assert "UPDATE" not in migration and "DELETE" not in migration and "ADD COLUMN" not in migration
