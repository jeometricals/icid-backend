"""
Reviewer edits groundwork (K0): the idr_field_edits table and its migration read as text, and the statements that
apply and log an edit, run against a patched query layer (no database).
"""

import re
from datetime import datetime, time, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from api.queries import idr_field_edits
from api.queries.idr_audit import EDIT_AUDIT_CTE
from api.queries.idr_field_edits import (
    AUDIT_ACTIONS, EDIT_STAGES, FIELD_EDIT_COLUMNS, append_pay_item, append_truck, apply_header_edit,
    apply_report_edit, list_field_edits, log_pay_item_approval,
)
from api.queries.idr_reports import REPORT_DATA_WITH_IDS, REPORT_DATA_WITH_PAY_ITEM_IDS, REPORT_DATA_WITH_TRUCK_IDS
from api.queries.idrs import HEADER_COLUMNS, submit_idr
from tests.test_auth_setup import sql, table_columns

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "migrations/020_field_edits.sql"
IDR_ID = UUID("9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e")
REPORT_ID = UUID("e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091")
EDITOR = UUID("f0000000-0000-4000-8000-000000000006")
ITEM_ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
EDIT_ROW = {"edit_id": UUID("aaaaaaaa-0000-4000-8000-000000000001"), "idr_id": IDR_ID, "report_id": REPORT_ID,
            "field_path": "description", "edit_type": "field_change", "old_value": "old", "new_value": "new",
            "editor_uuid": EDITOR, "editor_stage": "stage1", "edited_at": datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)}


def flat(text: str) -> str:
    """
    Put SQL on one line.
    Takes the text.
    Returns it with every run of whitespace as one space.
    """
    return re.sub(r"\s+", " ", text).strip()


def run(call, *args, result=None) -> tuple[str, tuple]:
    """
    Call a query helper with run_query patched.
    Takes the helper, its arguments and what run_query returns (one edit row unless given).
    Returns (the SQL on one line, its parameters).
    """
    with patch.object(idr_field_edits, "run_query", return_value=[EDIT_ROW] if result is None else result) as query:
        call(*args)
    statement, params = query.call_args.args
    return flat(statement), params


def unwrapped(params: tuple) -> list:
    """
    Read a statement's parameters with the JSON ones opened.
    Takes the parameter tuple.
    Returns a list where each Jsonb is ("json", its value) and everything else is itself.
    """
    return [("json", p.obj) if isinstance(p, Jsonb) else p for p in params]


# ---------------------------------------------------------------------------
# The table, in schema.sql and in the migration
# ---------------------------------------------------------------------------

class TestFieldEditsSchema:
    def table(self, name: str) -> str:
        """
        Cut the idr_field_edits table and its two indexes out of a SQL file, on one line.
        Takes the file's path under the repo root.
        Returns the statements with IF NOT EXISTS removed, so the migration and schema.sql can be compared.
        """
        text = flat(sql(name)).replace("IF NOT EXISTS ", "")
        start = text.index("CREATE TABLE icid.idr_field_edits")
        return text[start:text.index(";", text.index("CREATE INDEX idx_idr_field_edits_field", start)) + 1]

    def test_the_migration_and_schema_sql_declare_the_same_table(self):
        assert (ROOT / MIGRATION).is_file()
        # but for the edit types migrations 021 and 023 added to the CHECKs since
        as_first_made = self.table("schema.sql").replace(", 'pay_item_approve', 'truck_add'", "").replace(
            "(edit_type IN ('pay_item_add', 'truck_add'))", "(edit_type = 'pay_item_add')")
        assert self.table(MIGRATION) == as_first_made

    def test_the_columns(self):
        assert table_columns("idr_field_edits") == {
            "edit_id": "UUID PRIMARY KEY DEFAULT gen_random_uuid()",
            "idr_id": "UUID NOT NULL REFERENCES icid.idrs(idr_id) ON DELETE CASCADE",
            "report_id": "UUID REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE",
            "field_path": "TEXT NOT NULL", "edit_type": "TEXT NOT NULL", "old_value": "JSONB",
            "new_value": "JSONB NOT NULL", "editor_uuid": "UUID NOT NULL REFERENCES icid.users(uuid)",
            "editor_stage": "TEXT NOT NULL", "edited_at": "TIMESTAMPTZ NOT NULL DEFAULT now()",
        }

    def test_the_checks_name_the_edit_types_and_stages_the_api_writes(self):
        table = self.table("schema.sql")
        types = re.search(r"chk_idr_field_edits_type CHECK \(edit_type IN \(([^)]+)\)", table).group(1)
        stages = re.search(r"chk_idr_field_edits_stage CHECK \(editor_stage IN \(([^)]+)\)", table).group(1)
        assert re.findall(r"'(\w+)'", types) == list(AUDIT_ACTIONS)
        assert re.findall(r"'(\w+)'", stages) == [stage for stage, _ in EDIT_STAGES.values()]

    def test_only_an_added_pay_item_or_truck_has_no_old_value(self):
        assert f"{OLD_VALUE_CHECK} );" in self.table("schema.sql")

    def test_an_idrs_edits_are_indexed_in_order_and_a_fields_together(self):
        table = self.table(MIGRATION)
        assert "CREATE INDEX idx_idr_field_edits_idr ON icid.idr_field_edits(idr_id, edited_at);" in table
        assert "CREATE INDEX idx_idr_field_edits_field ON icid.idr_field_edits(report_id, field_path);" in table

    def test_the_api_names_only_columns_the_table_has(self):
        columns = set(table_columns("idr_field_edits"))
        assert {name.strip() for name in FIELD_EDIT_COLUMNS.split(",")} == columns
        inserted = re.search(r"INSERT INTO icid\.idr_field_edits \(([^)]+)\)", flat(idr_field_edits._log_ctes())).group(1)
        assert set(inserted.split(", ")) == columns - {"edit_id", "edited_at"}  # those two have defaults


class TestMigration020:
    def test_it_is_one_transaction_and_safe_to_rerun(self):
        migration = sql(MIGRATION)
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        assert migration.index("BEGIN;") < migration.index("CREATE TABLE") < migration.index("UPDATE") < migration.rindex("COMMIT;")
        assert "CREATE TABLE IF NOT EXISTS icid.idr_field_edits" in migration
        assert migration.count("CREATE INDEX IF NOT EXISTS") == migration.count("CREATE INDEX") == 2

    def test_the_backfill_gives_an_id_only_to_pay_items_without_one(self):
        migration = flat(sql(MIGRATION))
        assert ("CASE WHEN jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id') "
                "THEN e.item || jsonb_build_object('id', gen_random_uuid()::text) ELSE e.item END ORDER BY e.ord") in migration
        # only reports that still hold a pay item without an id are written at all: a second run changes nothing
        assert ("WHERE jsonb_typeof(r.report_data->'payItems') = 'array' AND EXISTS ( SELECT 1 FROM "
                "jsonb_array_elements(r.report_data->'payItems') AS e(item) WHERE jsonb_typeof(e.item) = 'object' "
                "AND NOT (e.item ? 'id') )") in migration

    def test_the_backfill_changes_nothing_else(self):
        migration = sql(MIGRATION)
        update = migration[migration.index("UPDATE icid.idr_reports"):migration.rindex("COMMIT;")]
        assert "updated_at" not in update and "SET report_data = jsonb_set(r.report_data, '{payItems}'" in flat(update)
        assert "DELETE FROM" not in migration and migration.count("UPDATE") == 1

    def test_it_gives_ids_the_way_submit_does(self):
        per_item = ("CASE WHEN jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id') THEN e.item || "
                    "jsonb_build_object('id', gen_random_uuid()::text) ELSE e.item END ORDER BY e.ord) FROM "
                    "jsonb_array_elements(r.report_data->'payItems') WITH ORDINALITY AS e(item, ord)")
        assert per_item in flat(sql(MIGRATION)) and per_item in flat(REPORT_DATA_WITH_PAY_ITEM_IDS)

    def test_it_carries_the_checks_that_no_pay_item_changed(self):
        text = (ROOT / MIGRATION).read_text(encoding="utf-8")
        assert text.count("md5(string_agg((e - 'id')::text") == 1 and "(run pre-check 3 again)" in text
        assert "count(DISTINCT e->>'id') AS distinct_ids" in text


# ---------------------------------------------------------------------------
# Submit gives every pay item an id
# ---------------------------------------------------------------------------

class TestSubmitGivesPayItemsIds:
    def submit_sql(self) -> str:
        """
        Capture the submit statement.
        Takes nothing.
        Returns its SQL on one line.
        """
        with patch("api.queries.idrs.run_query", return_value=[]) as query:
            submit_idr(IDR_ID, "idrs/x/inspector_1.png", EDITOR)
        return flat(query.call_args.args[0])

    def test_the_numbering_update_also_stamps_the_ids(self):
        statement = self.submit_sql()
        assert "SET page_number = o.page_number, updated_at = now(), report_data = CASE WHEN" in statement
        assert flat(REPORT_DATA_WITH_PAY_ITEM_IDS) in statement

    def test_a_conc_mix_gets_ids_on_its_trucks_and_any_other_report_on_its_pay_items(self):
        statement = self.submit_sql()
        assert flat(REPORT_DATA_WITH_IDS) in statement
        assert flat(REPORT_DATA_WITH_IDS) == (f"CASE WHEN r.report_type = 'CONC_MIX' THEN "
                                              f"{flat(REPORT_DATA_WITH_TRUCK_IDS)} ELSE "
                                              f"{flat(REPORT_DATA_WITH_PAY_ITEM_IDS)} END")
        assert flat(REPORT_DATA_WITH_TRUCK_IDS) == flat(REPORT_DATA_WITH_PAY_ITEM_IDS).replace("payItems", "trucks")
        assert statement.count("UPDATE icid.idr_reports") == 1  # one update of each report, not two in one statement

    def test_a_report_without_pay_items_keeps_its_data_as_it_is(self):
        expression = flat(REPORT_DATA_WITH_PAY_ITEM_IDS)
        assert expression.startswith("CASE WHEN jsonb_typeof(r.report_data->'payItems') = 'array' AND "
                                     "jsonb_array_length(r.report_data->'payItems') > 0 THEN")
        assert expression.endswith("ELSE r.report_data END")  # no list, or an empty one: jsonb_agg of nothing is NULL

    def test_an_item_that_has_an_id_keeps_it(self):
        assert "AND NOT (e.item ? 'id') THEN e.item ||" in flat(REPORT_DATA_WITH_PAY_ITEM_IDS)


# ---------------------------------------------------------------------------
# Applying and logging an edit, in one statement
# ---------------------------------------------------------------------------

class TestApplyReportEdit:
    args = (IDR_ID, REPORT_ID, "workforce.foremen", ["workforce", "foremen"], "field_change", "2", "3", EDITOR,
            "stage1_review", True)

    def test_one_statement_locks_writes_logs_and_audits(self):
        statement, _ = run(apply_report_edit, *self.args)
        assert statement.count(";") == 1 and statement.startswith("WITH target AS (")
        order = [statement.index(part) for part in (
            "FOR UPDATE", "UPDATE icid.idr_reports r", "UPDATE icid.idrs i", "INSERT INTO icid.idr_field_edits",
            "INSERT INTO icid.idr_audit", "SELECT edit_id,")]
        assert order == sorted(order)

    def test_the_idr_must_still_be_in_the_stage_under_this_reviewer(self):
        statement, params = run(apply_report_edit, *self.args)
        assert ("WHERE idr_id = %s AND status = %s AND deleted_at IS NULL AND stage1_reviewer_uuid = %s FOR UPDATE"
                in statement)
        assert params[:3] == (IDR_ID, "stage1_review", EDITOR)

    def test_stage_two_checks_the_re_reviewer(self):
        statement, params = run(apply_report_edit, *self.args[:8], "stage2_review", True)
        assert "AND re_reviewer_uuid = %s FOR UPDATE" in statement and params[1] == "stage2_review"

    def test_an_admin_is_not_held_to_the_reviewer(self):
        statement, params = run(apply_report_edit, *self.args[:9], False)
        assert "reviewer_uuid = %s" not in statement
        assert params[:2] == (IDR_ID, "stage1_review") and len(params) == len(run(apply_report_edit, *self.args)[1]) - 1

    def test_the_new_value_is_written_into_the_report_only_over_the_old_one(self):
        statement, params = run(apply_report_edit, *self.args)
        assert "SET report_data = jsonb_set(r.report_data, %s, %s, false), updated_at = now()" in statement
        assert "WHERE r.idr_id = t.idr_id AND r.report_id = %s AND r.report_data #> %s = %s" in statement
        assert unwrapped(params)[3:8] == [["workforce", "foremen"], ("json", "3"), REPORT_ID, ["workforce", "foremen"],
                                          ("json", "2")]

    def test_jsonb_set_never_creates_a_field_that_isnt_there(self):
        assert "jsonb_set(r.report_data, %s, %s, false)" in run(apply_report_edit, *self.args)[0]

    def test_nothing_is_logged_unless_the_report_changed(self):
        statement, _ = run(apply_report_edit, *self.args)
        assert "WHERE i.idr_id IN (SELECT idr_id FROM target) AND EXISTS (SELECT 1 FROM changed)" in statement
        assert "SELECT m.idr_id, %s, %s, %s, %s, %s, %s, %s FROM moved m" in statement  # no moved row, no edit row
        assert "FROM edit e JOIN target t ON t.idr_id = e.idr_id" in statement  # no edit row, no audit row

    def test_the_edit_row_records_old_and_new_the_editor_and_the_stage(self):
        _, params = run(apply_report_edit, *self.args)
        assert unwrapped(params)[8:] == [REPORT_ID, "workforce.foremen", "field_change", ("json", "2"), ("json", "3"),
                                         EDITOR, "stage1", "field_edit"]

    def test_the_audit_note_points_at_the_edit_rather_than_copying_it(self):
        audit = flat(EDIT_AUDIT_CTE)
        assert "jsonb_build_object('edit_id', e.edit_id, 'field_path', e.field_path)::text" in audit
        assert "old_value" not in audit and "new_value" not in audit
        assert "t.from_status, t.from_status" in audit  # an edit changes no status

    def test_a_pay_quantity_revision_is_logged_as_one(self):
        path = f"payItems[{ITEM_ID}].payQuantity"
        _, params = run(apply_report_edit, IDR_ID, REPORT_ID, path, ["payItems", "2", "payQuantity"],
                        "pay_item_revision", "60.00", "55.00", EDITOR, "stage2_review", True)
        values = unwrapped(params)
        assert values[3] == ["payItems", "2", "payQuantity"]  # where it is in the list today
        assert values[8:] == [REPORT_ID, path, "pay_item_revision", ("json", "60.00"), ("json", "55.00"), EDITOR,
                              "stage2", "pay_item_revise"]

    @pytest.mark.parametrize("old,new", [(None, "Y"), ("Y", None), (True, False), ("", "text"), (3, 4.5)])
    def test_any_json_scalar_can_be_the_old_or_new_value(self, old, new):
        _, params = run(apply_report_edit, IDR_ID, REPORT_ID, "safetyChecks.plates", ["safetyChecks", "plates"],
                        "field_change", old, new, EDITOR, "stage1_review", True)
        values = unwrapped(params)
        # a JSON null is a value, sent as JSON, never as SQL NULL: the field must exist and hold null to match
        assert values[4] == ("json", new) and values[7] == ("json", old)
        assert values[11:13] == [("json", old), ("json", new)]

    def test_it_returns_what_the_statement_returns(self):
        with patch.object(idr_field_edits, "run_query", return_value=[]):
            assert apply_report_edit(*self.args) == []  # the IDR moved on, or the field changed under us
        with patch.object(idr_field_edits, "run_query", return_value=[EDIT_ROW]):
            assert apply_report_edit(*self.args) == [EDIT_ROW]


class TestApplyHeaderEdit:
    def test_the_column_is_written_only_over_the_old_value(self):
        statement, params = run(apply_header_edit, IDR_ID, "weather_am", "Clear", "Rain", EDITOR, "stage1_review", True)
        assert "UPDATE icid.idrs i SET weather_am = %s, updated_at = now() FROM target t" in statement
        assert "WHERE i.idr_id = t.idr_id AND i.weather_am IS NOT DISTINCT FROM %s RETURNING i.*" in statement
        assert "idr_reports" not in statement
        assert unwrapped(params) == [IDR_ID, "stage1_review", EDITOR, "Rain", "Clear", None, "header.weather_am",
                                     "field_change", ("json", "Clear"), ("json", "Rain"), EDITOR, "stage1", "field_edit"]

    def test_a_time_or_a_number_is_compared_as_itself_and_logged_as_json(self):
        _, params = run(apply_header_edit, IDR_ID, "work_start_time", time(7, 0), time(7, 30), EDITOR,
                        "stage2_review", False)
        values = unwrapped(params)
        assert values[2:4] == [time(7, 30), time(7, 0)]  # the column's own type
        assert values[7:9] == [("json", "07:00:00"), ("json", "07:30:00")]  # text in the log
        _, params = run(apply_header_edit, IDR_ID, "temp_high", 74.5, None, EDITOR, "stage1_review", True)
        assert unwrapped(params)[3:5] == [None, 74.5] and unwrapped(params)[8:10] == [("json", 74.5), ("json", None)]

    @pytest.mark.parametrize("column", HEADER_COLUMNS)
    def test_every_header_field_can_be_edited(self, column):
        statement, params = run(apply_header_edit, IDR_ID, column, None, None, EDITOR, "stage1_review", True)
        assert f"SET {column} = %s" in statement and f"header.{column}" in params

    @pytest.mark.parametrize("column", ["report_date", "status", "idr_number", "inspector_signature_path",
                                        "weather_am = 'x'; --"])
    def test_nothing_else_on_the_idr_can(self, column):
        with patch.object(idr_field_edits, "run_query") as query:
            with pytest.raises(ValueError, match="Not a header column"):
                apply_header_edit(IDR_ID, column, "a", "b", EDITOR, "stage1_review", True)
        query.assert_not_called()


class TestAppendPayItem:
    item = {"id": ITEM_ID, "itemNo": "4.13 AAS", "budgetCode": "12345", "payQuantity": "40.00", "unit": "S.F.",
            "description": "Sidewalk"}

    def test_the_item_goes_on_the_end_of_the_list_and_is_logged_as_an_add(self):
        statement, params = run(append_pay_item, IDR_ID, REPORT_ID, self.item, EDITOR, "stage1_review", True)
        assert ("SET report_data = jsonb_set( r.report_data, '{payItems}', (CASE WHEN "
                "jsonb_typeof(r.report_data->'payItems') = 'array' THEN r.report_data->'payItems' ELSE '[]'::jsonb END) "
                "|| jsonb_build_array(%s::jsonb)), updated_at = now()") in statement
        assert unwrapped(params) == [IDR_ID, "stage1_review", EDITOR, ("json", self.item), REPORT_ID, REPORT_ID,
                                     f"payItems[{ITEM_ID}]", "pay_item_add", None, ("json", self.item), EDITOR,
                                     "stage1", "pay_item_add"]

    def test_there_was_no_old_value(self):
        _, params = run(append_pay_item, IDR_ID, REPORT_ID, self.item, EDITOR, "stage2_review", False)
        assert params[7] is None  # SQL NULL, as the table's CHECK requires of an add

    def test_an_item_without_an_id_is_refused_before_anything_runs(self):
        with patch.object(idr_field_edits, "run_query") as query:
            with pytest.raises(ValueError, match="needs an id"):
                append_pay_item(IDR_ID, REPORT_ID, {**self.item, "id": ""}, EDITOR, "stage1_review", True)
        query.assert_not_called()


class TestAppendTruck:
    truck = {"id": ITEM_ID, "truckOrTicketNo": "T-103", "slump": "4", "inspectionSticker": "Y"}

    def test_the_truck_goes_on_the_end_of_the_list_and_is_logged_as_an_add_under_its_id(self):
        statement, params = run(append_truck, IDR_ID, REPORT_ID, self.truck, EDITOR, "stage1_review", True)
        assert ("SET report_data = jsonb_set( r.report_data, '{trucks}', (CASE WHEN "
                "jsonb_typeof(r.report_data->'trucks') = 'array' THEN r.report_data->'trucks' ELSE '[]'::jsonb END) "
                "|| jsonb_build_array(%s::jsonb)), updated_at = now()") in statement
        assert unwrapped(params) == [IDR_ID, "stage1_review", EDITOR, ("json", self.truck), REPORT_ID, REPORT_ID,
                                     f"trucks[{ITEM_ID}]", "truck_add", None, ("json", self.truck), EDITOR,
                                     "stage1", "truck_add"]

    def test_it_is_one_statement_on_a_locked_idr_like_an_added_pay_item(self):
        statement, _ = run(append_truck, IDR_ID, REPORT_ID, self.truck, EDITOR, "stage1_review", True)
        item, _ = run(append_pay_item, IDR_ID, REPORT_ID, self.truck, EDITOR, "stage1_review", True)
        assert statement == item.replace("payItems", "trucks")
        assert statement.count(";") == 1 and "FOR UPDATE" in statement

    def test_there_was_no_old_value(self):
        _, params = run(append_truck, IDR_ID, REPORT_ID, self.truck, EDITOR, "stage2_review", False)
        assert params[7] is None  # SQL NULL, as the table's CHECK requires of an add

    def test_a_truck_without_an_id_is_refused_before_anything_runs(self):
        with patch.object(idr_field_edits, "run_query") as query:
            with pytest.raises(ValueError, match="needs an id"):
                append_truck(IDR_ID, REPORT_ID, {"slump": "4"}, EDITOR, "stage1_review", True)
        query.assert_not_called()


class TestListFieldEdits:
    def test_an_idrs_edits_come_oldest_first_with_the_editors_name(self):
        named = {**EDIT_ROW, "editor_first_name": "Olive", "editor_last_name": "Engineer"}
        with patch.object(idr_field_edits, "run_query", return_value=[named]) as query:
            assert list_field_edits(IDR_ID) == [named]
        statement, params = flat(query.call_args.args[0]), query.call_args.args[1]
        assert "FROM icid.idr_field_edits e JOIN icid.users u ON u.uuid = e.editor_uuid" in statement
        assert "u.first_name AS editor_first_name, u.last_name AS editor_last_name" in statement
        assert statement.endswith("WHERE e.idr_id = %s ORDER BY e.edited_at, e.edit_id;") and params == (IDR_ID,)

    def test_no_edits_is_an_empty_list_and_a_failure_is_none(self):
        for result in ([], None):
            with patch.object(idr_field_edits, "run_query", return_value=result):
                assert list_field_edits(IDR_ID) == result


# ---------------------------------------------------------------------------
# Migration 021 and the approval statement
# ---------------------------------------------------------------------------

MIGRATION_021 = "migrations/021_pay_item_approve.sql"
TYPE_CHECK = ("CONSTRAINT chk_idr_field_edits_type CHECK (edit_type IN ('field_change', 'pay_item_revision', "
              "'pay_item_add', 'pay_item_approve'))")


class TestMigration021:
    def test_it_widens_the_type_check_to_what_schema_sql_declares(self):
        assert (ROOT / MIGRATION_021).is_file()
        migration = flat(sql(MIGRATION_021))
        drop = "ALTER TABLE icid.idr_field_edits DROP CONSTRAINT IF EXISTS chk_idr_field_edits_type;"
        add = f"ALTER TABLE icid.idr_field_edits ADD {TYPE_CHECK};"
        assert migration.index(drop) < migration.index(add)

    def test_it_touches_nothing_else(self):
        migration = sql(MIGRATION_021)
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1
        assert migration.count("ALTER TABLE") == 2 and "UPDATE" not in migration and "CREATE" not in migration
        assert "chk_idr_field_edits_old_value" not in migration  # an approval has an old value, like every edit but an add

    def test_the_api_logs_an_approval_under_its_own_action(self):
        assert list(AUDIT_ACTIONS)[:4] == ["field_change", "pay_item_revision", "pay_item_add", "pay_item_approve"]
        assert AUDIT_ACTIONS["pay_item_approve"] == "pay_item_approve"


class TestLogPayItemApproval:
    args = (IDR_ID, REPORT_ID, f"payItems[{ITEM_ID}]", ["payItems", "2", "payQuantity"], "60.00", EDITOR,
            "stage1_review", True)

    def test_nothing_in_the_report_or_the_idr_is_written(self):
        statement, _ = run(log_pay_item_approval, *self.args)
        assert "UPDATE icid." not in statement and "jsonb_set" not in statement
        assert statement.count(";") == 1 and "FOR UPDATE" in statement  # still one statement, on a locked IDR

    def test_it_only_goes_through_while_the_item_still_holds_the_quantity_approved(self):
        statement, params = run(log_pay_item_approval, *self.args)
        assert ("moved AS ( SELECT t.idr_id FROM target t WHERE EXISTS ( SELECT 1 FROM icid.idr_reports r WHERE "
                "r.idr_id = t.idr_id AND r.report_id = %s AND r.report_data #> %s = %s ) )") in statement
        assert unwrapped(params)[3:6] == [REPORT_ID, ["payItems", "2", "payQuantity"], ("json", "60.00")]

    def test_the_row_holds_the_quantity_as_both_old_and_new(self):
        _, params = run(log_pay_item_approval, *self.args)
        assert unwrapped(params)[6:] == [REPORT_ID, f"payItems[{ITEM_ID}]", "pay_item_approve", ("json", "60.00"),
                                         ("json", "60.00"), EDITOR, "stage1", "pay_item_approve"]

    def test_it_is_held_to_the_stages_reviewer_like_any_edit(self):
        statement, params = run(log_pay_item_approval, *self.args)
        assert "AND stage1_reviewer_uuid = %s FOR UPDATE" in statement and params[:3] == (IDR_ID, "stage1_review", EDITOR)
        statement, params = run(log_pay_item_approval, *self.args[:6], "stage2_review", False)
        assert "reviewer_uuid = %s" not in statement and params[:2] == (IDR_ID, "stage2_review")

    def test_it_is_audited_in_the_same_statement(self):
        statement, _ = run(log_pay_item_approval, *self.args)
        assert statement.index("INSERT INTO icid.idr_field_edits") < statement.index("INSERT INTO icid.idr_audit")



# ---------------------------------------------------------------------------
# Migration 023: reviewer-added trucks
# ---------------------------------------------------------------------------

MIGRATION_023 = "migrations/023_truck_add.sql"
TYPE_CHECK_023 = ("CONSTRAINT chk_idr_field_edits_type CHECK (edit_type IN ('field_change', 'pay_item_revision', "
                  "'pay_item_add', 'pay_item_approve', 'truck_add'))")
OLD_VALUE_CHECK = ("CONSTRAINT chk_idr_field_edits_old_value CHECK ((old_value IS NULL) = "
                   "(edit_type IN ('pay_item_add', 'truck_add')))")


class TestMigration023:
    def test_it_widens_both_checks_to_what_schema_sql_declares(self):
        assert (ROOT / MIGRATION_023).is_file()
        migration, schema = flat(sql(MIGRATION_023)), flat(sql("schema.sql"))
        for name, check in (("chk_idr_field_edits_type", TYPE_CHECK_023),
                            ("chk_idr_field_edits_old_value", OLD_VALUE_CHECK)):
            drop = f"ALTER TABLE icid.idr_field_edits DROP CONSTRAINT IF EXISTS {name};"
            assert migration.index(drop) < migration.index(f"ALTER TABLE icid.idr_field_edits ADD {check};")
            assert check in schema

    def test_an_approval_still_needs_an_old_value(self):
        assert "'pay_item_approve'" not in OLD_VALUE_CHECK and OLD_VALUE_CHECK.count("'") == 4

    def test_it_gives_every_truck_an_id_the_way_submit_does(self):
        per_truck = ("CASE WHEN jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id') THEN e.item || "
                     "jsonb_build_object('id', gen_random_uuid()::text) ELSE e.item END ORDER BY e.ord) FROM "
                     "jsonb_array_elements(r.report_data->'trucks') WITH ORDINALITY AS e(item, ord)")
        assert per_truck in flat(sql(MIGRATION_023)) and per_truck in flat(REPORT_DATA_WITH_TRUCK_IDS)

    def test_the_backfill_is_the_pay_items_one_over_trucks(self):
        def update(path: str) -> str:
            text = sql(path)
            return flat(text[text.index("UPDATE icid.idr_reports"):text.rindex("COMMIT;")])
        assert update(MIGRATION_023) == update(MIGRATION).replace("payItems", "trucks")

    def test_the_backfill_changes_nothing_else_and_a_second_run_changes_nothing(self):
        migration = sql(MIGRATION_023)
        update = migration[migration.index("UPDATE icid.idr_reports"):migration.rindex("COMMIT;")]
        assert "updated_at" not in update and "SET report_data = jsonb_set(r.report_data, '{trucks}'" in flat(update)
        assert "WHERE jsonb_typeof(e.item) = 'object' AND NOT (e.item ? 'id')" in flat(update)
        assert "DELETE FROM" not in migration and migration.count("UPDATE") == 1
        assert migration.count("BEGIN;") == migration.count("COMMIT;") == 1 and migration.count("ALTER TABLE") == 4

    def test_it_carries_the_checks_that_no_truck_and_nothing_else_changed(self):
        text = (ROOT / MIGRATION_023).read_text(encoding="utf-8")
        assert "THEN e - 'id' ELSE e END)::text" in text and "(run pre-check 4 again)" in text
        assert "(r.report_data - 'trucks')::text || r.updated_at::text" in text and "(run pre-check 5 again)" in text
        assert "count(DISTINCT e->>'id') AS distinct_ids" in text and "(run pre-check 2 again)" in text

    def test_the_api_logs_an_added_truck_under_its_own_action(self):
        from api.queries.idr_field_edits import ADDED_TO
        assert AUDIT_ACTIONS["truck_add"] == "truck_add"
        assert ADDED_TO == {"pay_item_add": "payItems", "truck_add": "trucks"}
