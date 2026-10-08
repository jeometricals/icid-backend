"""
Quantities: reading an IDR's pay items as rows (api/services/quantities.py), the statement that replaces an IDR's
rows (api/queries/quantities.py), and migration 025 against schema.sql.
"""

import logging
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from api.queries.quantities import (
    delete_quantities_cte, insert_quantities_cte, quantity_rows_json, replace_quantities, replace_quantities_ctes,
)
from api.services import quantities
from api.services.quantities import (
    PAY_ITEM_REPORT_TYPES, canonical_unit, extract_rows, parse_amount, write_quantities,
)

ROOT = Path(__file__).resolve().parents[1]
IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
REPORTER = UUID("c0000000-0000-4000-8000-000000000003")
IDR = {"idr_id": IDR_ID, "project_id": "HWS0023", "reporter_uuid": REPORTER, "report_date": date(2026, 10, 5),
       "status": "approved"}


def pay_item(item_no="4.01 AAS", quantity="60.00", unit="L.F.", budget_code="12345",
             description="Concrete curb", **more) -> dict:
    """
    Build a pay item as a report stores one.
    Takes its item number, quantity, unit, budget code and description, and any other keys.
    Returns the item.
    """
    return {"id": "3f2a1b4c", "itemNo": item_no, "budgetCode": budget_code, "payQuantity": quantity, "unit": unit,
            "description": description, **more}


def report(report_type: str, number: int = 1, pay_items=None, **overrides) -> dict:
    """
    Build an idr_reports row.
    Takes its type, a number for its id, its pay items (no payItems key when None) and any column overrides.
    Returns the row.
    """
    data = {"description": "Work"} if pay_items is None else {"description": "Work", "payItems": pay_items}
    return {"report_id": UUID(int=number), "idr_id": IDR_ID, "report_type": report_type, "is_addendum": False,
            "is_auto_generated": False, "report_data": data, **overrides}


def row(report_type="SWCB", item="4.01 AAS", amount="60.00", unit="LF", budget_code="12345",
        description="Concrete curb") -> dict:
    """
    Build the quantity row extract_rows gives for a pay item of IDR.
    Takes the report type and the item's fields as they are written to the table.
    Returns the row.
    """
    return {"project_id": "HWS0023", "idr_id": IDR_ID, "report_date": date(2026, 10, 5), "reporter_uuid": REPORTER,
            "report_type": report_type, "pay_item_ref": item, "budget_code": budget_code, "description": description,
            "amount": Decimal(amount), "unit": unit}


# ---------------------------------------------------------------------------
# extract_rows
# ---------------------------------------------------------------------------

class TestExtractRows:
    def test_the_report_types_with_pay_items(self):
        assert PAY_ITEM_REPORT_TYPES == ("GEN", "SWCB", "AC")

    @pytest.mark.parametrize("report_type", ["GEN", "SWCB", "AC"])
    def test_a_pay_item_on_each_kind_of_report_becomes_a_row(self, report_type):
        assert extract_rows(IDR, [report(report_type, pay_items=[pay_item()])]) == [row(report_type)]

    def test_every_item_of_every_report_comes_out_in_report_and_item_order(self):
        reports = [
            report("GEN", 1, [pay_item("6.02 AAA", "2", "Each", description="Hydrant")]),
            report("SWCB", 2, [pay_item(), pay_item("4.13 AAA", "410.5", "S.F.", "67890", "Sidewalk")]),
            report("AC", 3, [pay_item("4.02 AB", "18.25", "Ton", None, "Binder course")]),
        ]
        assert extract_rows(IDR, reports) == [
            row("GEN", "6.02 AAA", "2", "EA", description="Hydrant"),
            row("SWCB"),
            row("SWCB", "4.13 AAA", "410.5", "SF", "67890", "Sidewalk"),
            row("AC", "4.02 AB", "18.25", "TN", None, "Binder course"),
        ]

    def test_the_same_item_twice_on_one_report_is_two_rows(self):
        rows = extract_rows(IDR, [report("SWCB", pay_items=[pay_item(quantity="10"), pay_item(quantity="15")])])
        assert [r["amount"] for r in rows] == [Decimal("10"), Decimal("15")]

    def test_an_auto_generated_general_is_left_out(self):
        # its items are the sums of the other reports', so counting them would count those twice
        reports = [report("GEN", 1, [pay_item(quantity="60.00")], is_auto_generated=True),
                   report("SWCB", 2, [pay_item(quantity="60.00")])]
        assert extract_rows(IDR, reports) == [row("SWCB")]

    def test_an_inspectors_own_general_counts(self):
        reports = [report("GEN", 1, [pay_item(quantity="5")]), report("SWCB", 2, [pay_item()])]
        assert [r["report_type"] for r in extract_rows(IDR, reports)] == ["GEN", "SWCB"]

    @pytest.mark.parametrize("report_type", ["CONC_MIX", "CONC_CYL", "SWR", "SKETCH"])
    def test_a_report_type_without_a_pay_items_table_gives_nothing(self, report_type):
        assert extract_rows(IDR, [report(report_type, pay_items=[pay_item()])]) == []

    def test_an_idr_with_only_conc_cyl_reports_or_none_gives_nothing(self):
        cylinders = {"cylinders": [{"id": "c1", "class": "4000", "cylinderNo": "C-1", "slump": "3.5"}]}
        assert extract_rows(IDR, [report("CONC_CYL", report_data=cylinders)]) == []
        assert extract_rows(IDR, []) == []

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_a_blank_quantity_is_skipped_with_a_warning(self, raw, caplog):
        with caplog.at_level(logging.WARNING, logger="api.services.quantities"):
            rows = extract_rows(IDR, [report("SWCB", 7, [pay_item(quantity=raw), pay_item("4.13 AAA", "5")])])
        assert [r["pay_item_ref"] for r in rows] == ["4.13 AAA"]
        assert (f"skipping pay-item row on IDR {IDR_ID}, report {UUID(int=7)}: quantity {raw!r} is not a number"
                in caplog.text)

    @pytest.mark.parametrize("raw", ["abc", "12 boxes", "1,2", "12.5.1", "1e5", "about 40", True])
    def test_a_quantity_that_isnt_a_number_is_skipped_with_a_warning(self, raw, caplog):
        with caplog.at_level(logging.WARNING, logger="api.services.quantities"):
            assert extract_rows(IDR, [report("SWCB", pay_items=[pay_item(quantity=raw)])]) == []
        assert f"quantity {raw!r} is not a number" in caplog.text

    def test_an_item_without_an_item_number_is_kept(self):
        added = pay_item(item_no="", budget_code="", description="Extra curb at the driveway", quantity="12")
        assert extract_rows(IDR, [report("SWCB", pay_items=[added])]) == [
            row(item=None, amount="12", budget_code=None, description="Extra curb at the driveway")]

    def test_text_fields_are_trimmed_and_blank_ones_are_null(self):
        item = pay_item(" 4.01 AAS ", " 60.00 ", " L.F. ", "  ", "  ")
        assert extract_rows(IDR, [report("SWCB", pay_items=[item])]) == [row(budget_code=None, description=None)]

    def test_a_quantity_saved_as_a_number_is_read_too(self):
        rows = extract_rows(IDR, [report("AC", pay_items=[pay_item(quantity=12.5), pay_item(quantity=3)])])
        assert [r["amount"] for r in rows] == [Decimal("12.5"), Decimal("3")]

    @pytest.mark.parametrize("data", [None, [], "text", {"payItems": "none"}, {"payItems": [None, "x", 3]}, {}])
    def test_report_data_of_any_shape_gives_nothing_and_never_crashes(self, data):
        assert extract_rows(IDR, [report("SWCB", report_data=data)]) == []

    def test_a_reviewers_value_is_what_is_read(self):
        # an edit is applied to report_data, so the report already holds the revised quantity
        assert extract_rows(IDR, [report("SWCB", pay_items=[pay_item(quantity="55.00")])])[0]["amount"] == Decimal("55.00")


# ---------------------------------------------------------------------------
# parse_amount and canonical_unit
# ---------------------------------------------------------------------------

class TestParseAmount:
    @pytest.mark.parametrize("raw,amount", [
        ("60", "60"), ("60.00", "60.00"), (" 12.5 ", "12.5"), ("1,200", "1200"), ("1,234,567.89", "1234567.89"),
        (".5", "0.5"), ("12.", "12"), ("0", "0"), ("-3.5", "-3.5"), (7, "7"), (7.25, "7.25"),
    ])
    def test_a_number_as_an_inspector_types_one(self, raw, amount):
        assert parse_amount(raw) == (Decimal(amount), None)

    @pytest.mark.parametrize("raw,amount,unit", [("125.5 SF", "125.5", "SF"), ("1,200 l.f.", "1200", "l.f."),
                                                 ("40LF", "40", "LF"), ("3 each", "3", "each")])
    def test_a_known_unit_typed_after_the_number_is_dropped_and_reported(self, raw, amount, unit):
        assert parse_amount(raw) == (Decimal(amount), unit)

    @pytest.mark.parametrize("raw", ["", " ", None, "abc", "SF 125", "12 boxes", "1,2", "12,34", "1,2345", "1 200",
                                     "12.5.1", "1e5", "$40", "40%", "nan", "inf", True, False])
    def test_anything_else_isnt_a_number(self, raw):
        assert parse_amount(raw) == (None, None)

    def test_a_unit_in_the_quantity_is_logged_when_the_row_is_read(self, caplog):
        with caplog.at_level(logging.WARNING, logger="api.services.quantities"):
            rows = extract_rows(IDR, [report("SWCB", 4, [pay_item(quantity="125.5 SF", unit="S.F.")])])
        assert (rows[0]["amount"], rows[0]["unit"]) == (Decimal("125.5"), "SF")  # the unit is the item's own
        assert f"pay-item row on IDR {IDR_ID}, report {UUID(int=4)}: quantity '125.5 SF' read as 125.5" in caplog.text


class TestCanonicalUnit:
    @pytest.mark.parametrize("unit,short", [
        ("L.F.", "LF"), ("LF", "LF"), ("lf", "LF"), ("S.F.", "SF"), ("SF", "SF"), ("C.Y.", "CY"), ("CY", "CY"),
        ("S.Y.", "SY"), ("SY", "SY"), ("Ton", "TN"), ("Tons", "TN"), ("T", "TN"), ("TN", "TN"), ("Each", "EA"),
        ("EA", "EA"), ("L.S.", "LS"), ("LS", "LS"), (" l. f. ", "LF"),
    ])
    def test_a_known_unit_takes_its_short_form(self, unit, short):
        assert canonical_unit(unit) == short

    @pytest.mark.parametrize("unit", ["", "  ", None])
    def test_no_unit_is_none(self, unit):
        assert canonical_unit(unit) is None

    def test_an_unknown_unit_is_kept_as_written_and_logged_once(self, caplog):
        with patch.object(quantities, "_unknown_units", set()), \
                caplog.at_level(logging.WARNING, logger="api.services.quantities"):
            assert [canonical_unit(unit) for unit in (" Gal. ", "Gal.", "Gal.", "Hrs")] == ["Gal.", "Gal.", "Gal.", "Hrs"]
        assert caplog.text.count("unknown pay-item unit 'Gal.' kept as written") == 1
        assert caplog.text.count("unknown pay-item unit 'Hrs' kept as written") == 1


# ---------------------------------------------------------------------------
# replace_quantities / write_quantities: one statement
# ---------------------------------------------------------------------------

def flat(sql: str) -> str:
    """
    Put a statement on one line, to compare it whatever its indentation.
    Takes the SQL.
    Returns it with every run of whitespace as one space.
    """
    return " ".join(sql.split())


class TestReplaceQuantities:
    def test_one_statement_deletes_the_idrs_rows_and_inserts_the_new_ones(self):
        with patch("api.queries.quantities.run_query", return_value=[{"inserted": 2}]) as query:
            assert replace_quantities(IDR_ID, [row(), row("AC", None, "3", None)]) == 2
        assert query.call_count == 1
        sql = flat(query.call_args.args[0])
        assert sql.startswith("WITH given AS ( SELECT %s::uuid AS idr_id ), deleted_quantities AS ( DELETE FROM "
                              "icid.quantities WHERE idr_id IN (SELECT idr_id FROM given) ), "
                              "inserted_quantities AS ( INSERT INTO icid.quantities (")
        assert sql.endswith("RETURNING quantity_id ) SELECT COUNT(*) AS inserted FROM inserted_quantities;")
        assert "FROM jsonb_to_recordset(%s) AS q(" in sql
        assert sql.count("icid.quantities") == 2 and sql.count(";") == 1

    def test_the_rows_go_in_as_one_json_parameter_and_every_row_takes_the_idr_given(self):
        with patch("api.queries.quantities.run_query", return_value=[{"inserted": 1}]) as query:
            replace_quantities(IDR_ID, [row(amount="1200.50")])
        idr, rows = query.call_args.args[1]
        assert idr == IDR_ID and "CROSS JOIN given i" in flat(query.call_args.args[0])
        assert isinstance(rows, Jsonb) and rows.obj == [{
            "project_id": "HWS0023", "report_date": "2026-10-05", "reporter_uuid": str(REPORTER),
            "report_type": "SWCB", "pay_item_ref": "4.01 AAS", "budget_code": "12345",
            "description": "Concrete curb", "amount": "1200.50", "unit": "LF"}]

    def test_every_column_the_table_takes_is_read_from_the_json_but_its_id_and_its_idr(self):
        columns = re.search(r"INSERT INTO icid\.quantities \((.*?)\)", flat(replace_quantities_ctes("given"))).group(1)
        inserted = [column.strip() for column in columns.split(",")]
        assert inserted == ["project_id", "idr_id", "report_date", "reporter_uuid", "report_type", "pay_item_ref",
                            "budget_code", "description", "amount", "unit"]
        assert set(quantity_rows_json([row()]).obj[0]) == set(inserted) - {"idr_id"}

    def test_no_rows_still_deletes_what_the_idr_had(self):
        with patch("api.queries.quantities.run_query", return_value=[{"inserted": 0}]) as query:
            assert replace_quantities(IDR_ID, []) == 0
        assert "DELETE FROM icid.quantities" in query.call_args.args[0] and query.call_args.args[1][1].obj == []

    def test_a_second_set_replaces_the_first(self):
        with patch("api.queries.quantities.run_query", side_effect=[[{"inserted": 2}], [{"inserted": 1}]]) as query:
            counts = [write_quantities(IDR_ID, [row(), row(item="4.13 AAA")]), write_quantities(IDR_ID, [row("AC")])]
        assert counts == [2, 1]
        first, second = (call.args for call in query.call_args_list)
        assert first[0] == second[0]  # the same statement: delete this IDR's rows, insert the ones given
        assert [r["report_type"] for r in second[1][1].obj] == ["AC"]

    def test_a_failed_statement_is_none(self):
        with patch("api.queries.quantities.run_query", return_value=None):
            assert replace_quantities(IDR_ID, [row()]) is None and write_quantities(IDR_ID, [row()]) is None

    def test_write_quantities_takes_what_extract_rows_gives(self):
        rows = extract_rows(IDR, [report("SWCB", pay_items=[pay_item()])])
        with patch("api.queries.quantities.run_query", return_value=[{"inserted": 1}]) as query:
            assert write_quantities(IDR_ID, rows) == 1
        assert query.call_args.args[1][1].obj[0]["amount"] == "60.00"

    def test_the_two_ctes_read_the_idr_from_the_cte_named_and_take_one_parameter_between_them(self):
        delete, insert = flat(delete_quantities_cte("moved")), flat(insert_quantities_cte("moved"))
        assert delete == "deleted_quantities AS ( DELETE FROM icid.quantities WHERE idr_id IN (SELECT idr_id FROM moved) )"
        assert "SELECT q.project_id, i.idr_id, q.report_date" in insert and insert.endswith(
            "CROSS JOIN moved i RETURNING quantity_id )")
        assert (delete.count("%s"), insert.count("%s")) == (0, 1)
        assert flat(replace_quantities_ctes("moved")) == f"{delete}, {insert}"


# ---------------------------------------------------------------------------
# Migration 025 and schema.sql
# ---------------------------------------------------------------------------

class TestMigration025:
    migration = (ROOT / "migrations" / "025_quantities.sql").read_text(encoding="utf-8")
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")

    def table(self, text: str) -> str:
        """
        Find the quantities table's definition in a file.
        Takes the file's text.
        Returns the CREATE TABLE statement, on one line.
        """
        return flat(re.search(r"CREATE TABLE icid\.quantities \(.*?\n\);", text, re.DOTALL).group(0))

    def test_schema_sql_has_the_table_the_migration_creates(self):
        assert self.table(self.schema) == self.table(self.migration)

    def test_the_table_is_in_the_icid_schema_with_its_keys(self):
        table = self.table(self.migration)
        assert "quantity_id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in table
        assert "project_id TEXT NOT NULL REFERENCES icid.projects(project_id)" in table
        assert "idr_id UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE" in table
        assert "amount NUMERIC NOT NULL" in table and "pay_item_ref TEXT," in table

    def test_both_files_have_the_four_indexes(self):
        indexes = ["CREATE INDEX idx_quantities_project_date ON icid.quantities(project_id, report_date);",
                   "CREATE INDEX idx_quantities_project_item ON icid.quantities(project_id, pay_item_ref);",
                   "CREATE INDEX idx_quantities_project_budget ON icid.quantities(project_id, budget_code);",
                   "CREATE INDEX idx_quantities_idr ON icid.quantities(idr_id);"]
        for index in indexes:
            assert index in self.migration and index in self.schema, index

    def test_the_statement_names_only_columns_the_table_has(self):
        table = self.table(self.migration)
        for column in ("project_id", "idr_id", "report_date", "reporter_uuid", "report_type", "pay_item_ref",
                       "budget_code", "description", "amount", "unit"):
            assert f" {column} " in table, column
