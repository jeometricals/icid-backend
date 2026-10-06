"""
Review workflow groundwork (J0): the idrs and project_users columns and their migrations, read as text (no database).
"""

import re
from datetime import date
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from api.queries.idrs import create_idr
from api.services.auth import PROJECT_ROLES
from tests.test_auth_setup import sql, table_columns
from tests.test_signatures_setup import added_columns

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_017 = "migrations/017_review_workflow.sql"
MIGRATION_018 = "migrations/018_project_roles.sql"
USERS_FK = "UUID REFERENCES icid.users(uuid)"
IDR_COLUMNS = {
    "idr_number": "TEXT", "stage1_reviewer_uuid": USERS_FK, "stage1_reviewed_at": "TIMESTAMPTZ",
    "re_reviewer_uuid": USERS_FK, "re_signature_path": "TEXT", "re_signed_at": "TIMESTAMPTZ",
    "return_reason": "TEXT", "returned_from": "TEXT", "deleted_at": "TIMESTAMPTZ", "deleted_by": USERS_FK,
}
STATUS_CHECK = ("CONSTRAINT chk_idrs_status CHECK (status IN ('draft', 'submitted', 'stage1_review', "
                "'stage2_review', 'approved', 'returned', 'deleted'))")
RETURNED_FROM_CHECK = "CONSTRAINT chk_idrs_returned_from CHECK (returned_from IN ('stage1', 'stage2'))"
NUMBER_INDEX = ("uq_idrs_project_number ON icid.idrs(project_id, idr_number) "
                "WHERE idr_number IS NOT NULL AND deleted_at IS NULL;")
DAY_INDEX = ("uq_idrs_project_reporter_date ON icid.idrs(project_id, reporter_uuid, report_date) "
             "WHERE deleted_at IS NULL;")
ROLE_CHECK = "CONSTRAINT chk_project_users_role CHECK (role IN ('inspector', 'oe', 're'))"
ROLE_KEY = "PRIMARY KEY (project_id, user_uuid, role)"


def flat(name: str) -> str:
    """
    Read a SQL file without its comments, on one line.
    Takes the file's path under the repo root.
    Returns its statements with every run of whitespace as one space.
    """
    return re.sub(r"\s+", " ", sql(name)).replace("( '", "('")


class TestReviewSchema:
    def test_idrs_has_the_review_columns_all_nullable(self):
        columns = table_columns("idrs")
        assert {name: columns[name] for name in IDR_COLUMNS} == IDR_COLUMNS  # no NOT NULL, no default

    def test_the_status_column_keeps_its_default_and_gains_the_review_statuses(self):
        assert table_columns("idrs")["status"] == "TEXT NOT NULL DEFAULT 'draft'"
        schema = flat("schema.sql")
        assert STATUS_CHECK in schema and RETURNED_FROM_CHECK in schema

    def test_an_idr_number_is_unique_per_project_until_its_idr_is_deleted(self):
        schema = flat("schema.sql")
        assert f"CREATE UNIQUE INDEX {NUMBER_INDEX}" in schema
        assert "CREATE INDEX idx_idrs_project_status ON icid.idrs(project_id, status) WHERE deleted_at IS NULL;" in schema
        assert "CREATE INDEX idx_idrs_status ON icid.idrs(status) WHERE deleted_at IS NULL;" in schema

    def test_one_idr_per_inspector_per_day_until_it_is_deleted(self):
        schema = flat("schema.sql")
        assert f"CREATE UNIQUE INDEX {DAY_INDEX}" in schema
        assert "CONSTRAINT uq_idrs_project_reporter_date" not in schema  # a partial index can't be a constraint

    def test_project_users_has_one_row_per_role(self):
        assert table_columns("project_users")["role"] == "TEXT NOT NULL DEFAULT 'inspector'"
        schema = flat("schema.sql")
        assert ROLE_CHECK in schema and f"{ROLE_KEY}," in schema
        assert "PRIMARY KEY (project_id, user_uuid)," not in schema
        assert table_columns("project_users")["user_role"] == "TEXT"  # the display label stays

    def test_the_role_check_lists_the_roles_the_api_knows(self):
        assert re.findall(r"'(\w+)'", ROLE_CHECK) == list(PROJECT_ROLES)


class TestMigration017:
    def test_the_file_exists_and_adds_what_schema_sql_declares(self):
        assert (ROOT / MIGRATION_017).is_file()
        assert added_columns(sql(MIGRATION_017), "idrs") == {
            name: definition.split()[0] for name, definition in IDR_COLUMNS.items()}
        migration = flat(MIGRATION_017)
        for name, definition in IDR_COLUMNS.items():
            assert f"ADD COLUMN IF NOT EXISTS {name} {definition}" in migration

    def test_status_is_widened_not_added(self):
        migration = flat(MIGRATION_017)
        assert "ADD COLUMN IF NOT EXISTS status" not in migration and "ADD COLUMN status" not in migration
        drop = "ALTER TABLE icid.idrs DROP CONSTRAINT IF EXISTS chk_idrs_status;"
        add = f"ALTER TABLE icid.idrs ADD {STATUS_CHECK};"
        assert migration.index(drop) < migration.index(add)

    def test_the_returned_from_check_comes_with_its_column(self):
        assert f"ADD COLUMN IF NOT EXISTS returned_from TEXT {RETURNED_FROM_CHECK}," in flat(MIGRATION_017)

    def test_the_indexes_match_schema_sql(self):
        migration, schema = flat(MIGRATION_017), flat("schema.sql")
        assert f"CREATE UNIQUE INDEX IF NOT EXISTS {NUMBER_INDEX}" in migration
        assert "CREATE INDEX IF NOT EXISTS idx_idrs_status ON icid.idrs(status) WHERE deleted_at IS NULL;" in migration
        rebuilt = "CREATE INDEX idx_idrs_project_status ON icid.idrs(project_id, status) WHERE deleted_at IS NULL;"
        assert rebuilt in migration and rebuilt in schema
        assert migration.index("DROP INDEX IF EXISTS icid.idx_idrs_project_status;") < migration.index(rebuilt)

    def test_the_per_day_rule_becomes_a_partial_index_of_the_same_name(self):
        migration = flat(MIGRATION_017)
        drop = "ALTER TABLE icid.idrs DROP CONSTRAINT IF EXISTS uq_idrs_project_reporter_date;"
        create = f"CREATE UNIQUE INDEX IF NOT EXISTS {DAY_INDEX}"
        assert migration.index("ADD COLUMN IF NOT EXISTS deleted_at") < migration.index(drop) < migration.index(create)

    def test_creating_an_idr_names_no_conflict_target(self):
        with patch("api.queries.idrs.run_query", return_value=[]) as run:
            create_idr("HWS0023", uuid4(), date(2026, 10, 6))
        # a column list alone no longer matches the partial index, and its WHERE names a column 017 adds
        assert "ON CONFLICT DO NOTHING" in run.call_args.args[0] and "ON CONFLICT (" not in run.call_args.args[0]

    def test_no_existing_row_is_touched(self):
        migration = sql(MIGRATION_017)
        assert "UPDATE" not in migration and "DELETE" not in migration
        assert "NOT NULL" not in migration.replace("IS NOT NULL", "")  # nothing is required of existing rows

    def test_it_is_one_transaction_and_safe_to_rerun(self):
        migration = sql(MIGRATION_017)
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        assert migration.index("BEGIN;") < migration.index("ALTER TABLE") < migration.rindex("COMMIT;")
        assert migration.count("ADD COLUMN") == migration.count("ADD COLUMN IF NOT EXISTS") == len(IDR_COLUMNS)

    def test_it_carries_the_status_pre_check(self):
        text = (ROOT / MIGRATION_017).read_text(encoding="utf-8")
        assert "SELECT status, count(*) FROM icid.idrs GROUP BY status ORDER BY status;" in text


class TestMigration018:
    def test_the_file_exists_and_adds_the_role_column_with_its_check(self):
        assert (ROOT / MIGRATION_018).is_file()
        column = f"ADD COLUMN IF NOT EXISTS role {table_columns('project_users')['role']} {ROLE_CHECK};"
        assert column in flat(MIGRATION_018)

    def test_the_primary_key_gains_the_role(self):
        migration = flat(MIGRATION_018)
        # the old key is found by type, whatever it is named, and dropped before the new one is added
        assert "WHERE conrelid = 'icid.project_users'::regclass AND contype = 'p';" in migration
        drop = "EXECUTE format('ALTER TABLE icid.project_users DROP CONSTRAINT %I', pk_name);"
        add = f"ALTER TABLE icid.project_users ADD CONSTRAINT project_users_pkey {ROLE_KEY};"
        assert migration.index("ADD COLUMN IF NOT EXISTS role") < migration.index(drop) < migration.index(add)

    def test_a_second_run_leaves_the_new_key_alone(self):
        assert "IF pk_name IS NOT NULL AND pk_columns = 3 THEN RETURN; END IF;" in flat(MIGRATION_018)

    def test_it_is_one_transaction_and_touches_no_rows(self):
        migration = sql(MIGRATION_018)
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        assert migration.index("BEGIN;") < migration.index("ALTER TABLE") < migration.rindex("COMMIT;")
        assert "UPDATE" not in migration and "DELETE" not in migration and "user_role" not in migration

    def test_the_seed_upserts_on_the_new_key(self):
        assert "ON CONFLICT (project_id, user_uuid, role) DO NOTHING;" in sql("seed_auth_users.sql")
