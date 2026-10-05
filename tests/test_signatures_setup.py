"""
Signatures groundwork (I0): the columns, their migration, the Storage bucket and the settings, read as text (no
database).
"""

import importlib
import re
from pathlib import Path

import pytest

from api.core import config
from tests.test_auth_setup import sql, table_columns

ROOT = Path(__file__).resolve().parents[1]
USER_COLUMNS = {"signature_path": "TEXT", "signature_type": "TEXT", "signature_set_at": "TIMESTAMPTZ"}
IDR_COLUMNS = {"inspector_signature_path": "TEXT", "inspector_signed_at": "TIMESTAMPTZ"}
SIGNATURE_TYPE_CHECK = "CONSTRAINT chk_users_signature_type CHECK (signature_type IN ('drawn', 'uploaded'))"


def added_columns(migration: str, table: str) -> dict[str, str]:
    """
    Read the columns one ALTER TABLE in a migration adds.
    Takes the migration's text (comments stripped) and the table's name within the icid schema.
    Returns {column name: its type}.
    """
    statement = re.search(rf"ALTER TABLE icid\.{table}\b(.*?);", migration, re.DOTALL).group(1)
    return dict(re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)\s+(\w+)", statement))


@pytest.fixture
def reload_config(monkeypatch):
    """
    Re-import api.core.config under a changed environment, ignoring any .env file, and restore it afterwards.
    Yields a function taking SIGNATURE_* values (None unsets one) and returning the reloaded module.
    """
    def reload(**env):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
        for name, value in env.items():
            monkeypatch.delenv(name, raising=False) if value is None else monkeypatch.setenv(name, value)
        return importlib.reload(config)

    yield reload
    monkeypatch.undo()
    importlib.reload(config)


class TestSignaturesSchema:
    def test_users_has_the_signature_columns_all_nullable(self):
        columns = table_columns("users")
        assert {name: columns[name] for name in USER_COLUMNS} == USER_COLUMNS  # bare types: no NOT NULL, no default

    def test_signature_type_is_drawn_or_uploaded(self):
        assert SIGNATURE_TYPE_CHECK in sql("schema.sql")

    def test_idrs_has_the_signed_copy_columns_all_nullable(self):
        columns = table_columns("idrs")
        assert {name: columns[name].removesuffix(" NULL") for name in IDR_COLUMNS} == IDR_COLUMNS

    def test_the_submit_columns_are_untouched(self):
        columns = table_columns("idrs")
        assert columns["status"] == "TEXT NOT NULL DEFAULT 'draft'"
        assert columns["submitted_at"] == "TIMESTAMPTZ NULL" and columns["total_pages"] == "INTEGER NULL"


class TestMigration015:
    def test_the_file_exists_and_adds_what_schema_sql_declares(self):
        assert (ROOT / "migrations" / "015_signatures.sql").is_file()
        migration = sql("migrations/015_signatures.sql")
        assert added_columns(migration, "users") == USER_COLUMNS
        assert added_columns(migration, "idrs") == IDR_COLUMNS

    def test_the_check_comes_with_the_signature_type_column(self):
        migration = re.sub(r"\s+", " ", sql("migrations/015_signatures.sql"))
        assert f"ADD COLUMN IF NOT EXISTS signature_type TEXT {SIGNATURE_TYPE_CHECK}," in migration

    def test_nothing_is_required_of_existing_rows(self):
        migration = sql("migrations/015_signatures.sql")
        assert "NOT NULL" not in migration and "DEFAULT" not in migration
        assert "UPDATE" not in migration and "DROP" not in migration

    def test_it_is_one_transaction_and_safe_to_rerun(self):
        migration = sql("migrations/015_signatures.sql")
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        assert migration.index("BEGIN;") < migration.index("ALTER TABLE") < migration.rindex("COMMIT;")
        assert migration.count("ADD COLUMN") == migration.count("ADD COLUMN IF NOT EXISTS") == 5


class TestSignaturesBucket:
    def test_the_bucket_is_private_png_only_and_500_kb(self):
        assert (ROOT / "migrations" / "015b_signatures_bucket.sql").is_file()
        bucket = re.sub(r"\s+", " ", sql("migrations/015b_signatures_bucket.sql"))
        assert "INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)" in bucket
        assert "VALUES ( 'signatures', 'signatures', false, 512000, ARRAY['image/png']::text[] )" in bucket

    def test_it_follows_the_exports_buckets_pattern(self):
        # like migration 012: re-running updates the settings instead of silently keeping old ones
        bucket, exports = sql("migrations/015b_signatures_bucket.sql"), sql("migrations/012_idr_exports_bucket.sql")
        assert bucket[bucket.index("ON CONFLICT"):].split() == exports[exports.index("ON CONFLICT"):].split()
        assert "icid." not in bucket  # no table changes


class TestSignaturesConfig:
    def test_the_settings_have_defaults(self, reload_config):
        settings = reload_config(SIGNATURE_BUCKET_NAME=None, SIGNATURE_URL_EXPIRY_SECONDS=None)
        assert (settings.SIGNATURE_BUCKET_NAME, settings.SIGNATURE_URL_EXPIRY_SECONDS) == ("signatures", 300)

    def test_the_settings_can_be_set(self, reload_config):
        settings = reload_config(SIGNATURE_BUCKET_NAME="sigs-staging", SIGNATURE_URL_EXPIRY_SECONDS="60")
        assert (settings.SIGNATURE_BUCKET_NAME, settings.SIGNATURE_URL_EXPIRY_SECONDS) == ("sigs-staging", 60)

    def test_the_default_bucket_is_the_one_the_migration_creates(self, reload_config):
        settings = reload_config(SIGNATURE_BUCKET_NAME=None)
        assert f"'{settings.SIGNATURE_BUCKET_NAME}'" in sql("migrations/015b_signatures_bucket.sql")
