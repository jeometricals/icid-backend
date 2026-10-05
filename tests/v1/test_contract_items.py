import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid5

from api.queries.contract_items import get_contract_item, list_contract_items_for_project
from api.queries.spec_items import get_spec_item, list_spec_items

# ---------------------------------------------------------------------------
# The DDL and seed files. Tests never hit the database, so table shape and seed
# content are checked against the files that create them.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT.joinpath("schema.sql").read_text(encoding="utf-8")
MIGRATION_SQL = ROOT.joinpath("migrations", "011_spec_items_and_contract_items.sql").read_text(encoding="utf-8")
SEED_SQL = ROOT.joinpath("seed_sidewalk_pay_items.sql").read_text(encoding="utf-8")

SEED_PROJECT_ID = "HWS0023"
EMPTY_PROJECT_ID = "SE384"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _table_body(sql: str, table: str) -> str:
    """
    Cut one CREATE TABLE statement's column list out of a DDL file.
    Takes the DDL text and the qualified table name (e.g. "icid.spec_items").
    Returns the text between the table name and the closing ");".
    """
    marker = re.search(rf"CREATE TABLE (IF NOT EXISTS )?{re.escape(table)} \(", sql)
    assert marker, f"{table} is not created"
    return sql[marker.end():].split(");", 1)[0]


def _columns(sql: str, table: str) -> dict[str, str]:
    """
    Map each column of a CREATE TABLE statement to the rest of its definition.
    Takes the DDL text and the qualified table name.
    Returns {column_name: definition} for column lines (constraint lines are skipped).
    """
    columns = {}
    for line in _table_body(sql, table).splitlines():
        line = line.strip().rstrip(",")
        if line and not line.startswith("CONSTRAINT"):
            name, definition = line.split(None, 1)
            columns[name] = definition
    return columns


def _seed_spec_items() -> list[tuple[str, str, str, str]]:
    """
    Parse the spec items out of the pay-item seed file.
    Takes nothing; reads SEED_SQL.
    Returns (item_no, description, spec_section, pay_unit) tuples in seed order.
    """
    section = SEED_SQL.split("INSERT INTO icid.spec_items", 1)[1].split(";", 1)[0]
    return re.findall(r"\('([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'\)", section)


def _seed_contract_items() -> list[tuple[str, str, Decimal, Decimal]]:
    """
    Parse the contract items out of the pay-item seed file.
    Takes nothing; reads SEED_SQL.
    Returns (item_no, budget_code, bid_quantity, bid_unit_price) tuples in seed order.
    """
    section = SEED_SQL.split("INSERT INTO icid.contract_items", 1)[1].split(";", 1)[0]
    rows = re.findall(r"\('([^']+)',\s*'([^']+)',\s*([\d.]+),\s*([\d.]+)\)", section)
    return [(item_no, budget, Decimal(quantity), Decimal(price)) for item_no, budget, quantity, price in rows]


def _seeded_rows() -> list[dict]:
    """
    Build the joined rows list_contract_items_for_project would return for the seeded project.
    Takes nothing; derives every row from the seed file, with stable fake uuids.
    Returns dict rows keyed like the query's SELECT, with NUMERIC columns as Decimal (as psycopg returns them).
    """
    specs = {item_no: (description, section, unit) for item_no, description, section, unit in _seed_spec_items()}
    namespace = UUID("00000000-0000-0000-0000-000000000011")
    rows = []
    for item_no, budget, quantity, price in _seed_contract_items():
        description, section, unit = specs[item_no]
        rows.append({
            "contract_item_id": uuid5(namespace, f"{item_no}|{budget}"),
            "project_id": SEED_PROJECT_ID,
            "spec_item_id": uuid5(namespace, item_no),
            "budget_code": budget,
            "bid_quantity": quantity,
            "bid_unit_price": price,
            "item_no": item_no,
            "description": description,
            "spec_section": section,
            "pay_unit": unit,
            "created_at": NOW,
            "updated_at": NOW,
        })
    return rows


# ---------------------------------------------------------------------------
# Schema: spec_items and contract_items (schema.sql and migrations/011)
# ---------------------------------------------------------------------------

class TestCatalogSchema:
    def test_spec_items_columns(self):
        columns = _columns(SCHEMA_SQL, "icid.spec_items")
        assert list(columns) == [
            "spec_item_id", "item_no", "description", "spec_section", "pay_unit", "created_at", "updated_at",
        ]
        assert columns["spec_item_id"].startswith("UUID PRIMARY KEY")
        for name in ("item_no", "description", "spec_section", "pay_unit"):
            assert columns[name] == "TEXT NOT NULL"
        assert "UNIQUE (item_no)" in _table_body(SCHEMA_SQL, "icid.spec_items")

    def test_contract_items_columns(self):
        columns = _columns(SCHEMA_SQL, "icid.contract_items")
        assert list(columns) == [
            "contract_item_id", "project_id", "spec_item_id", "budget_code",
            "bid_quantity", "bid_unit_price", "created_at", "updated_at",
        ]
        # project_id matches icid.projects' TEXT key, and a project's items go with it.
        assert columns["project_id"] == "TEXT NOT NULL REFERENCES icid.projects(project_id) ON DELETE CASCADE"
        assert columns["spec_item_id"] == "UUID NOT NULL REFERENCES icid.spec_items(spec_item_id)"
        assert columns["budget_code"] == "TEXT NOT NULL"
        assert columns["bid_quantity"] == "NUMERIC(12,2) NOT NULL"
        assert columns["bid_unit_price"] == "NUMERIC(12,2) NOT NULL"

    def test_contract_items_unique_on_project_spec_item_and_budget_code(self):
        # The same spec item may appear under several budget codes, but never twice under one.
        for sql in (SCHEMA_SQL, MIGRATION_SQL):
            assert "UNIQUE (project_id, spec_item_id, budget_code)" in _table_body(sql, "icid.contract_items")

    def test_migration_matches_schema(self):
        for table in ("icid.spec_items", "icid.contract_items"):
            assert _columns(MIGRATION_SQL, table) == _columns(SCHEMA_SQL, table)
        for index in ("idx_contract_items_project ON icid.contract_items(project_id)",
                      "idx_contract_items_spec_item ON icid.contract_items(spec_item_id)"):
            assert index in SCHEMA_SQL
            assert index in MIGRATION_SQL


# ---------------------------------------------------------------------------
# Seed: seed_sidewalk_pay_items.sql
# ---------------------------------------------------------------------------

class TestPayItemSeed:
    def test_seeds_22_unique_spec_items(self):
        item_nos = [item[0] for item in _seed_spec_items()]
        assert len(item_nos) == 22
        assert len(set(item_nos)) == 22

    def test_spec_items_carry_their_pay_unit(self):
        units = {item_no: unit for item_no, _, _, unit in _seed_spec_items()}
        assert units["4.13 AAS"] == "S.F."
        assert units["4.02 CA"] == "Ton"
        assert units["6.87"] == "Each"
        assert set(units.values()) == {"S.Y.", "Ton", "C.Y.", "L.F.", "S.F.", "Each"}

    def test_seeds_23_contract_items_for_the_sidewalk_project(self):
        rows = _seed_contract_items()
        assert len(rows) == 23
        assert "SELECT 'HWS0023', s.spec_item_id" in SEED_SQL
        assert {item_no for item_no, *_ in rows} == {item[0] for item in _seed_spec_items()}

    def test_seed_respects_the_compound_unique_key(self):
        keys = [(item_no, budget) for item_no, budget, *_ in _seed_contract_items()]
        assert len(keys) == len(set(keys))

    def test_4_13_aas_seeded_under_two_budget_codes(self):
        rows = [row for row in _seed_contract_items() if row[0] == "4.13 AAS"]
        assert [(budget, price) for _, budget, _, price in rows] == [
            ("12345", Decimal("14.50")),
            ("67890", Decimal("19.00")),
        ]


# ---------------------------------------------------------------------------
# Query layer
# ---------------------------------------------------------------------------

class TestContractItemQueries:
    def test_list_joins_spec_items_and_filters_by_project(self):
        rows = _seeded_rows()
        with patch("api.queries.contract_items.run_query", return_value=rows) as mock:
            result = list_contract_items_for_project(SEED_PROJECT_ID)
        sql, params = mock.call_args.args
        assert "FROM icid.contract_items ci" in sql
        assert "JOIN icid.spec_items si ON si.spec_item_id = ci.spec_item_id" in sql
        assert "si.pay_unit" in sql
        assert "WHERE ci.project_id = %s" in sql
        assert params == (SEED_PROJECT_ID,)
        assert result[0]["pay_unit"] == "S.Y."

    def test_list_passes_failure_through_as_none(self):
        with patch("api.queries.contract_items.run_query", return_value=None):
            assert list_contract_items_for_project(SEED_PROJECT_ID) is None

    def test_get_returns_the_joined_row_or_none(self):
        row = _seeded_rows()[0]
        with patch("api.queries.contract_items.run_query", return_value=[row]) as mock:
            assert get_contract_item(row["contract_item_id"]) == row
        assert "WHERE ci.contract_item_id = %s" in mock.call_args.args[0]
        with patch("api.queries.contract_items.run_query", return_value=[]):
            assert get_contract_item(row["contract_item_id"]) is None


class TestSpecItemQueries:
    def test_list_orders_by_item_no(self):
        with patch("api.queries.spec_items.run_query", return_value=[]) as mock:
            assert list_spec_items() == []
        assert "FROM icid.spec_items" in mock.call_args.args[0]
        assert "ORDER BY item_no" in mock.call_args.args[0]

    def test_get_returns_the_row_or_none(self):
        spec_item_id = UUID("6c1f0a2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b")
        row = {"spec_item_id": spec_item_id, "item_no": "4.13 AAS", "pay_unit": "S.F."}
        with patch("api.queries.spec_items.run_query", return_value=[row]) as mock:
            assert get_spec_item(spec_item_id) == row
        assert mock.call_args.args[1] == (spec_item_id,)
        with patch("api.queries.spec_items.run_query", return_value=[]):
            assert get_spec_item(spec_item_id) is None


# ---------------------------------------------------------------------------
# GET /v1/contract_items/?project_id=
# ---------------------------------------------------------------------------

class TestListContractItems:
    url = "/v1/contract_items/"

    def test_returns_the_23_seeded_items(self, admin_client):
        with patch("api.queries.contract_items.run_query", return_value=_seeded_rows()):
            response = admin_client.get(self.url, params={"project_id": SEED_PROJECT_ID})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert len(body["data"]) == 23

    def test_items_carry_spec_fields_inline(self, admin_client):
        with patch("api.queries.contract_items.run_query", return_value=_seeded_rows()):
            item = admin_client.get(self.url, params={"project_id": SEED_PROJECT_ID}).json()["data"][0]
        assert set(item) == {
            "contract_item_id", "project_id", "spec_item_id", "budget_code", "bid_quantity", "bid_unit_price",
            "item_no", "description", "spec_section", "pay_unit", "created_at", "updated_at",
        }
        assert item["item_no"] == "4.02 AB-R"
        assert item["pay_unit"] == "S.Y."
        assert item["bid_quantity"] == 2500.0  # psycopg's Decimal goes out as a JSON number
        assert item["bid_unit_price"] == 18.5

    def test_4_13_aas_appears_under_both_budget_codes(self, admin_client):
        with patch("api.queries.contract_items.run_query", return_value=_seeded_rows()):
            data = admin_client.get(self.url, params={"project_id": SEED_PROJECT_ID}).json()["data"]
        aas = [item for item in data if item["item_no"] == "4.13 AAS"]
        assert [(i["budget_code"], i["bid_unit_price"]) for i in aas] == [("12345", 14.5), ("67890", 19.0)]
        assert aas[0]["spec_item_id"] == aas[1]["spec_item_id"]
        assert aas[0]["contract_item_id"] != aas[1]["contract_item_id"]
        assert {i["pay_unit"] for i in aas} == {"S.F."}

    def test_empty_list_for_project_without_contract_items(self, admin_client):
        with patch("api.queries.contract_items.run_query", return_value=[]) as mock:
            response = admin_client.get(self.url, params={"project_id": EMPTY_PROJECT_ID})
        assert response.status_code == 200
        assert response.json()["data"] == []
        assert mock.call_args.args[1] == (EMPTY_PROJECT_ID,)

    def test_query_failure_is_500(self, admin_client):
        with patch("api.queries.contract_items.run_query", return_value=None):
            response = admin_client.get(self.url, params={"project_id": SEED_PROJECT_ID})
        assert response.status_code == 500

    def test_project_id_is_required(self, admin_client):
        assert admin_client.get(self.url).status_code == 422
