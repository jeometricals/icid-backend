from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from api.services.field_edits import FieldEditError, _initials, parse_report_path, resolve_report_path
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# ---------------------------------------------------------------------------

IDR_ID = "9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e"
SWCB_ID = "e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091"
GEN_ID = "4e5f6071-8293-4a41-b5c6-d7e8f9a0b1c2"
MIX_ID = "5a0e8c1d-0000-4000-8000-00000000000a"
ITEM_1 = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
ITEM_2 = "7c8d9e0f-1a2b-4c3d-8e4f-5a6b7c8d9e0f"
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)

# Two reviewers on HWS0023, neither an admin
OLIVE = {"uuid": UUID("f0000000-0000-4000-8000-000000000006"), "email": "olive@icid.local", "first_name": "Olive",
         "last_name": "Engineer", "client_id": "C00001", "role": None, "is_demo": False, "signature_path": None,
         "signature_type": None, "signature_set_at": None}
REX = {**OLIVE, "uuid": UUID("f0000000-0000-4000-8000-000000000007"), "email": "rex@icid.local", "first_name": "Rex",
       "last_name": "Resident"}

STAGE1_IDR = {
    "idr_id": UUID(IDR_ID), "project_id": "HWS0023", "reporter_uuid": UUID("c0000000-0000-4000-8000-000000000003"),
    "report_date": date(2026, 10, 5), "work_start_time": time(7, 0), "work_end_time": None,
    "inspector_start_time": None, "inspector_end_time": None, "temp_low": Decimal("58.0"), "temp_high": Decimal("74.5"),
    "weather_am": "Clear", "weather_pm": None, "total_pages": 3, "has_dismissed_auto_general": False,
    "status": "stage1_review", "submitted_at": NOW, "created_at": NOW, "updated_at": NOW,
    "inspector_signature_path": None, "inspector_signed_at": None, "idr_number": "005",
    "stage1_reviewer_uuid": OLIVE["uuid"], "stage1_reviewed_at": None, "re_reviewer_uuid": None,
    "re_signature_path": None, "re_signed_at": None, "return_reason": None, "returned_from": None,
    "deleted_at": None, "deleted_by": None,
}
STAGE2_IDR = {**STAGE1_IDR, "status": "stage2_review", "re_reviewer_uuid": REX["uuid"]}


def report(report_id: str, report_type: str, report_data: dict, **overrides) -> dict:
    """
    Build an idr_reports row.
    Takes the report's id, type and report_data, and any column overrides.
    Returns the row.
    """
    return {"report_id": UUID(report_id), "idr_id": UUID(IDR_ID), "report_type": report_type, "is_addendum": False,
            "parent_report_id": None, "page_number": 1, "report_data": report_data, "is_auto_generated": False,
            "created_at": NOW, "updated_at": NOW, **overrides}


SWCB_DATA = {
    "description": "Poured curb along Main St.",
    "comments": "",
    "structural": False,
    "workforce": {"superintendent": "1", "foremen": "2", "operators": "", "laborers": "6", "flaggers": ""},
    "additionalWorkforce": [{"label": "Mason", "count": "3"}, {"label": "Surveyor", "count": "1"}],
    "equipment": {"backhoe": {"model": "CAT 420", "number": "1"}},
    "safetyChecks": {"plasticBarrels": "Y", "plates": None},
    "safetyRemarks": {"plates": ""},
    "payItems": [
        {"id": ITEM_1, "itemNo": "4.13 AAS", "budgetCode": "12345", "payQuantity": "60.00", "unit": "S.F.",
         "description": "Sidewalk"},
        {"id": ITEM_2, "itemNo": "4.08 AA", "budgetCode": "12345", "payQuantity": "29.00", "unit": "L.F.",
         "description": "Curb"},
    ],
}
SWCB = report(SWCB_ID, "SWCB", SWCB_DATA, page_number=2)
GENERAL = report(GEN_ID, "GEN", {"description": "General notes", "payItems": []})
AUTO_GENERAL = {**GENERAL, "is_auto_generated": True}
CONC_MIX = report(MIX_ID, "CONC_MIX", {"remarks": "ok", "trucks": [{"slump": "4"}]}, is_addendum=True,
                  parent_report_id=UUID(SWCB_ID), page_number=3)

EDIT_ROW = {"edit_id": UUID("aaaaaaaa-0000-4000-8000-000000000001"), "idr_id": UUID(IDR_ID),
            "report_id": UUID(SWCB_ID), "field_path": "workforce.foremen", "edit_type": "field_change",
            "old_value": "2", "new_value": "3", "editor_uuid": OLIVE["uuid"], "editor_stage": "stage1", "edited_at": NOW}
LISTED_EDIT = {**EDIT_ROW, "editor_first_name": "Olive", "editor_last_name": "Engineer"}

FIELD_URL = f"/v1/idrs/{IDR_ID}/field"
ADD_URL = f"/v1/idrs/{IDR_ID}/pay-items/add"
NOT_THE_REVIEWER = {"detail": "Only the reviewer who accepted this IDR can edit it"}
NOT_IN_REVIEW = {"detail": "Only an IDR in review can be edited by a reviewer"}
CHANGED = {"detail": "The IDR changed while you were editing; reload and try again"}


def revise_url(item_id: str = ITEM_1) -> str:
    """
    Build the revise route's URL for a pay item.
    Takes the item's id.
    Returns the path.
    """
    return f"/v1/idrs/{IDR_ID}/pay-items/{item_id}/revise"


def approve_url(item_id: str = ITEM_1) -> str:
    """
    Build the approve route's URL for a pay item.
    Takes the item's id.
    Returns the path.
    """
    return f"/v1/idrs/{IDR_ID}/pay-items/{item_id}/approve"


@contextmanager
def backend(idr=STAGE1_IDR, reports=(GENERAL, SWCB, CONC_MIX), roles=("oe",), applied="edit", edits=(LISTED_EDIT,),
            accepted_at=None):
    """
    Patch the query layer under the edit routes.
    Takes the IDR row every read returns (None: no such IDR), the IDR's reports, the roles the caller holds on its project, what an edit statement returns ("edit": one edit row; or [] / None), the edits the list returns and when the IDR's stage was last accepted.
    Yields a dict: "writes" is the list of (sql on one line, params with JSON opened) of every edit statement run, "regen" the mock of the auto-General rebuild.
    """
    seen = {"writes": []}

    def reports_query(sql, params=None):
        """Stand in for run_query in api.queries.idr_reports."""
        if "report_type = 'GEN'" in sql:
            return [r for r in reports if r["report_type"] == "GEN" and not r["is_addendum"]]
        if "report_id = %s" in sql:
            return [r for r in reports if r["report_id"] == params[1]]
        return list(reports)

    def edits_query(sql, params=None):
        """Stand in for run_query in api.queries.idr_field_edits."""
        if "INSERT INTO icid.idr_field_edits" in sql:
            opened = [("json", p.obj) if isinstance(p, Jsonb) else p for p in params]
            seen["writes"].append((" ".join(sql.split()), opened))
            return [EDIT_ROW] if applied == "edit" else applied
        return list(edits)

    with patch("api.queries.idrs.run_query", side_effect=lambda sql, params=None: [idr] if idr else []), \
         patch("api.queries.projects.run_query", return_value=[{"role": role} for role in roles]), \
         patch("api.queries.idr_reports.run_query", side_effect=reports_query), \
         patch("api.queries.idr_field_edits.run_query", side_effect=edits_query), \
         patch("api.queries.idr_audit.run_query", return_value=[{"at": accepted_at}]), \
         patch("api.services.field_edits.regenerate_auto_general") as regen:
        seen["regen"] = regen
        yield seen


def field(path: str, value, report_id: str = SWCB_ID) -> dict:
    """
    Build a field-edit body.
    Takes the field_path, the new value and the report (None for a header field).
    Returns the JSON body.
    """
    return {"report_id": report_id, "field_path": path, "new_value": value}


# ---------------------------------------------------------------------------
# PATCH /v1/idrs/{idr_id}/field: a field of a report
# ---------------------------------------------------------------------------

class TestEditReportField:
    def test_the_stage_one_reviewer_edits_a_field_and_gets_the_idr_back_with_its_edits(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field("workforce.foremen", "3"))
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Field edited"
        assert body["data"]["idr_id"] == IDR_ID and len(body["data"]["reports"]) == 3
        assert body["data"]["field_edits"] == [{
            "edit_id": "aaaaaaaa-0000-4000-8000-000000000001", "report_id": SWCB_ID, "field_path": "workforce.foremen",
            "edit_type": "field_change", "old_value": "2", "new_value": "3", "editor_uuid": str(OLIVE["uuid"]),
            "editor_stage": "stage1", "edited_at": "2026-10-06T15:00:00Z", "editor_name": "Olive Engineer",
            "editor_initials": "OE"}]
        assert len(seen["writes"]) == 1

    def test_the_statement_gets_the_path_the_old_value_read_from_the_report_and_the_new_one(self):
        with signed_in(OLIVE) as client, backend() as seen:
            client.patch(FIELD_URL, json=field("workforce.foremen", "3"))
        sql, params = seen["writes"][0]
        assert "SET report_data = jsonb_set(r.report_data, %s, %s, false)" in sql and "AND stage1_reviewer_uuid = %s" in sql
        assert params == [UUID(IDR_ID), "stage1_review", OLIVE["uuid"], ["workforce", "foremen"], ("json", "3"),
                          UUID(SWCB_ID), ["workforce", "foremen"], ("json", "2"),
                          UUID(SWCB_ID), "workforce.foremen", "field_change", ("json", "2"), ("json", "3"),
                          OLIVE["uuid"], "stage1", "field_edit"]

    @pytest.mark.parametrize("path,json_path,old,new", [
        ("description", ["description"], "Poured curb along Main St.", "Poured curb along Main St. and Elm St."),
        ("structural", ["structural"], False, True),
        ("safetyChecks.plasticBarrels", ["safetyChecks", "plasticBarrels"], "Y", "N"),
        ("safetyChecks.plates", ["safetyChecks", "plates"], None, "Y"),  # a checklist cell left unanswered
        ("safetyChecks.plasticBarrels", ["safetyChecks", "plasticBarrels"], "Y", None),  # and one cleared
        ("equipment.backhoe.model", ["equipment", "backhoe", "model"], "CAT 420", "CAT 430"),
        ("additionalWorkforce[1].count", ["additionalWorkforce", "1", "count"], "1", "2"),  # a list row, by position
        (f"payItems[{ITEM_2}].budgetCode", ["payItems", "1", "budgetCode"], "12345", "67890"),  # a pay item, by id
    ])
    def test_every_kind_of_field_resolves_to_its_place_in_the_report(self, path, json_path, old, new):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field(path, new))
        assert response.status_code == 200, response.json()
        _, params = seen["writes"][0]
        assert params[3:5] == [json_path, ("json", new)] and params[7] == ("json", old)
        assert params[9:11] == [path, "field_change"]

    def test_a_pay_quantity_edited_here_is_a_pay_item_revision(self):
        path = f"payItems[{ITEM_1}].payQuantity"
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=field(path, "55.00")).status_code == 200
        _, params = seen["writes"][0]
        assert params[3] == ["payItems", "0", "payQuantity"]
        assert params[9:13] == [path, "pay_item_revision", ("json", "60.00"), ("json", "55.00")]
        assert params[-1] == "pay_item_revise"

    def test_the_re_reviewer_edits_at_stage_two_and_the_edit_is_stamped_stage_two(self):
        with signed_in(REX) as client, backend(idr=STAGE2_IDR, roles=("re",)) as seen:
            assert client.patch(FIELD_URL, json=field("workforce.foremen", "4")).status_code == 200
        sql, params = seen["writes"][0]
        assert "AND re_reviewer_uuid = %s" in sql
        assert params[:3] == [UUID(IDR_ID), "stage2_review", REX["uuid"]] and params[-3:] == [REX["uuid"], "stage2", "field_edit"]

    def test_a_chain_of_edits_comes_back_oldest_first_each_with_its_own_initials(self):
        second = {**LISTED_EDIT, "edit_id": UUID("aaaaaaaa-0000-4000-8000-000000000002"), "old_value": "3",
                  "new_value": "4", "editor_uuid": REX["uuid"], "editor_stage": "stage2",
                  "editor_first_name": "Rex", "editor_last_name": "Resident"}
        with signed_in(REX) as client, backend(idr=STAGE2_IDR, roles=("re",), edits=(LISTED_EDIT, second)):
            edits = client.patch(FIELD_URL, json=field("workforce.foremen", "4")).json()["data"]["field_edits"]
        assert [(e["old_value"], e["new_value"], e["editor_initials"], e["editor_stage"]) for e in edits] == [
            ("2", "3", "OE", "stage1"), ("3", "4", "RR", "stage2")]

    def test_an_admin_edits_in_the_reviewers_place_and_is_not_held_to_the_reviewer_column(self, admin_client):
        with backend(roles=()) as seen:
            assert admin_client.patch(FIELD_URL, json=field("workforce.foremen", "3")).status_code == 200
        sql, params = seen["writes"][0]
        assert "reviewer_uuid = %s" not in sql
        assert params[:2] == [UUID(IDR_ID), "stage1_review"] and params[-3] == ADMIN_USER_ROW["uuid"]

    @pytest.mark.parametrize("path", [
        "nothing", "workforce.plumbers", "workforce.foremen.count", "additionalWorkforce[2].count",
        "additionalWorkforce[x].count", "description[0]", f"payItems[{ITEM_1}].colour",
        "payItems[0].payQuantity", "payItems[no-such-id].payQuantity",
    ])
    def test_a_path_that_leads_nowhere_in_this_report_is_400(self, path):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field(path, "x"))
        assert response.status_code == 400 and response.json() == {"detail": f"This report has no field {path}"}
        assert seen["writes"] == []

    @pytest.mark.parametrize("path", ["workforce", "payItems", "additionalWorkforce", "additionalWorkforce[0]",
                                      f"payItems[{ITEM_1}]", "equipment.backhoe"])
    def test_a_whole_object_list_or_pay_item_is_not_a_field(self, path):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field(path, "x"))
        assert response.status_code == 400 and response.json() == {"detail": f"{path} is not a single field"}
        assert seen["writes"] == []

    @pytest.mark.parametrize("path", ["", ".", "workforce..foremen", "work force.foremen", "workforce.foremen;--",
                                      "payItems[].payQuantity", "a[1][2]", "1abc", "workforce.foremen "])
    def test_text_that_isnt_a_path_is_400(self, path):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field(path, "x"))
        assert response.status_code == 400 and response.json() == {"detail": f"Not a field path: {path}"}
        assert seen["writes"] == []

    def test_a_pay_items_id_cant_be_edited(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field(f"payItems[{ITEM_1}].id", "other"))
        assert response.status_code == 400 and response.json() == {"detail": "A pay item's id can't be edited"}
        assert seen["writes"] == []

    @pytest.mark.parametrize("value", [{"a": 1}, ["3"], [], {}])
    def test_the_new_value_must_be_a_single_value(self, value):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field("workforce.foremen", value))
        assert response.status_code == 400 and "single value" in response.json()["detail"]
        assert seen["writes"] == []

    def test_the_same_value_again_is_400_and_logs_nothing(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field("workforce.foremen", "2"))
        assert response.status_code == 400 and response.json() == {"detail": "The field already holds that value"}
        assert seen["writes"] == []

    def test_a_number_where_text_was_is_a_change(self):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=field("workforce.foremen", 2)).status_code == 200
        assert seen["writes"][0][1][4] == ("json", 2) and seen["writes"][0][1][7] == ("json", "2")

    def test_a_report_that_isnt_in_the_idr_is_404(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=field("description", "x", report_id=IDR_ID))
        assert response.status_code == 404 and response.json() == {"detail": "Report not found in this IDR"}
        assert seen["writes"] == []

    def test_a_field_changed_under_the_reviewer_is_409(self):
        with signed_in(OLIVE) as client, backend(applied=[]) as seen:
            response = client.patch(FIELD_URL, json=field("workforce.foremen", "3"))
        assert response.status_code == 409 and response.json() == CHANGED
        seen["regen"].assert_not_called()

    def test_a_failed_statement_is_500(self):
        with signed_in(OLIVE) as client, backend(applied=None):
            response = client.patch(FIELD_URL, json=field("workforce.foremen", "3"))
        assert response.status_code == 500 and response.json() == {"detail": "Failed to save the edit"}

    @pytest.mark.parametrize("body", [{}, {"report_id": SWCB_ID}, {"report_id": "not-a-uuid", "field_path": "x"},
                                      {"field_path": 7}])
    def test_a_malformed_body_is_422(self, body):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=body).status_code == 422
        assert seen["writes"] == []


# ---------------------------------------------------------------------------
# PATCH /v1/idrs/{idr_id}/field: a header field
# ---------------------------------------------------------------------------

class TestEditHeaderField:
    def header(self, column: str, value) -> dict:
        """
        Build a header-edit body.
        Takes the header column and the new value.
        Returns the JSON body (no report_id).
        """
        return {"field_path": f"header.{column}", "new_value": value}

    def test_text_is_written_over_the_old_text(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=self.header("weather_am", "Rain"))
        assert response.status_code == 200
        sql, params = seen["writes"][0]
        assert "UPDATE icid.idrs i SET weather_am = %s" in sql and "idr_reports" not in sql
        assert params == [UUID(IDR_ID), "stage1_review", OLIVE["uuid"], "Rain", "Clear", None, "header.weather_am",
                          "field_change", ("json", "Clear"), ("json", "Rain"), OLIVE["uuid"], "stage1", "field_edit"]

    def test_a_time_sent_as_text_is_stored_as_a_time_and_logged_as_text(self):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=self.header("work_start_time", "07:30")).status_code == 200
        params = seen["writes"][0][1]
        assert params[3:5] == [time(7, 30), time(7, 0)]
        assert params[8:10] == [("json", "07:00:00"), ("json", "07:30:00")]

    def test_a_temperature_is_a_number_in_the_column_and_in_the_log(self):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=self.header("temp_high", 80)).status_code == 200
        params = seen["writes"][0][1]
        assert params[3:5] == [80.0, Decimal("74.5")]  # compared against the NUMERIC column as it was read
        assert params[8:10] == [("json", 74.5), ("json", 80.0)]

    def test_a_field_can_be_filled_or_cleared(self):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=self.header("weather_pm", "Cloudy")).status_code == 200
            assert client.patch(FIELD_URL, json=self.header("weather_am", None)).status_code == 200
        assert seen["writes"][0][1][3:5] == ["Cloudy", None] and seen["writes"][1][1][3:5] == [None, "Clear"]

    @pytest.mark.parametrize("column,value", [("work_start_time", "half past seven"), ("temp_low", "warm"),
                                              ("work_end_time", "25:00")])
    def test_a_value_the_field_cant_take_is_400(self, column, value):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=self.header(column, value))
        assert response.status_code == 400 and response.json()["detail"].startswith(f"header.{column}: ")
        assert seen["writes"] == []

    def test_the_low_cant_pass_the_high(self):
        with signed_in(OLIVE) as client, backend() as seen:
            too_high = client.patch(FIELD_URL, json=self.header("temp_low", 90))
            too_low = client.patch(FIELD_URL, json=self.header("temp_high", 40))
        for response in (too_high, too_low):
            assert response.status_code == 400
            assert response.json() == {"detail": "temp_low cannot be greater than temp_high"}
        assert seen["writes"] == []

    @pytest.mark.parametrize("column,value", [("weather_am", "Clear"), ("work_start_time", "07:00:00"),
                                              ("temp_high", 74.5), ("weather_pm", None)])
    def test_the_same_value_again_is_400(self, column, value):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=self.header(column, value))
        assert response.status_code == 400 and response.json() == {"detail": "The field already holds that value"}
        assert seen["writes"] == []

    @pytest.mark.parametrize("column", ["report_date", "status", "idr_number", "inspector_signature_path",
                                        "re_signature_path", "stage1_reviewer_uuid", "deleted_at"])
    def test_nothing_but_the_eight_header_fields_can_be_edited(self, column):
        # the work date above all: a wrong date is a return to the inspector, not an edit
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.patch(FIELD_URL, json=self.header(column, "x"))
        assert response.status_code == 400 and response.json() == {"detail": f"Not a header field: header.{column}"}
        assert seen["writes"] == []

    def test_a_header_path_with_a_report_or_a_report_path_without_one_is_400(self):
        mismatch = {"detail": "A header field takes no report_id; any other field needs one"}
        with signed_in(OLIVE) as client, backend() as seen:
            with_report = client.patch(FIELD_URL, json=field("header.weather_am", "Rain"))
            without = client.patch(FIELD_URL, json={"field_path": "description", "new_value": "x"})
        assert (with_report.status_code, with_report.json()) == (400, mismatch)
        assert (without.status_code, without.json()) == (400, mismatch)
        assert seen["writes"] == []


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/pay-items/{pay_item_id}/revise
# ---------------------------------------------------------------------------

class TestRevisePayItem:
    def test_the_item_is_found_by_its_id_in_whichever_report_holds_it(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(revise_url(ITEM_2), json={"revised_quantity": "31.50"})
        assert response.status_code == 200 and response.json()["message"] == "Pay item revised"
        sql, params = seen["writes"][0]
        assert params[3:8] == [["payItems", "1", "payQuantity"], ("json", "31.50"), UUID(SWCB_ID),
                               ["payItems", "1", "payQuantity"], ("json", "29.00")]
        assert params[8:] == [UUID(SWCB_ID), f"payItems[{ITEM_2}].payQuantity", "pay_item_revision", ("json", "29.00"),
                              ("json", "31.50"), OLIVE["uuid"], "stage1", "pay_item_revise"]

    def test_only_the_quantity_changes_the_inspectors_is_kept_in_the_log(self):
        with signed_in(OLIVE) as client, backend() as seen:
            client.post(revise_url(), json={"revised_quantity": "55.00"})
        sql, params = seen["writes"][0]
        assert sql.count("jsonb_set") == 1 and params[3] == ["payItems", "0", "payQuantity"]  # one field of one item
        assert params[11] == ("json", "60.00")  # old_value: what the inspector entered

    @pytest.mark.parametrize("sent,stored", [("  55.00 ", "55.00"), (55, "55"), (55.5, "55.5"), ("0", "0")])
    def test_the_quantity_is_stored_as_text_like_the_forms(self, sent, stored):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.post(revise_url(), json={"revised_quantity": sent}).status_code == 200
        assert seen["writes"][0][1][4] == ("json", stored)

    @pytest.mark.parametrize("sent", ["", "   "])
    def test_a_blank_quantity_is_400(self, sent):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(revise_url(), json={"revised_quantity": sent})
        assert response.status_code == 400 and response.json() == {"detail": "A quantity is required"}
        assert seen["writes"] == []

    def test_the_same_quantity_is_400(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(revise_url(), json={"revised_quantity": "60.00"})
        assert response.status_code == 400 and seen["writes"] == []

    def test_an_item_no_report_of_the_idr_holds_is_404(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(revise_url("no-such-item"), json={"revised_quantity": "1"})
        assert response.status_code == 404 and response.json() == {"detail": "Pay item not found in this IDR"}
        assert seen["writes"] == []

    def test_a_second_revision_starts_from_the_first(self):
        revised = {**SWCB, "report_data": {**SWCB_DATA, "payItems": [{**SWCB_DATA["payItems"][0], "payQuantity": "55.00"},
                                                                     SWCB_DATA["payItems"][1]]}}
        with signed_in(REX) as client, backend(idr=STAGE2_IDR, roles=("re",), reports=(GENERAL, revised)) as seen:
            assert client.post(revise_url(), json={"revised_quantity": "57.25"}).status_code == 200
        params = seen["writes"][0][1]
        assert params[11:13] == [("json", "55.00"), ("json", "57.25")] and params[-2] == "stage2"

    @pytest.mark.parametrize("body", [{}, {"revised_quantity": None}, {"revised_quantity": ["1"]}])
    def test_a_malformed_body_is_422(self, body):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.post(revise_url(), json=body).status_code == 422
        assert seen["writes"] == []


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/pay-items/add
# ---------------------------------------------------------------------------

NEW_ITEM = {"report_id": SWCB_ID, "item_no": "4.05 A", "budget_code": "12345", "quantity": "12.50", "unit": "C.Y.",
            "description": "Concrete base"}


class TestAddPayItem:
    def test_the_item_joins_the_end_of_the_reports_pay_items_with_a_fresh_id(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(ADD_URL, json=NEW_ITEM)
        assert response.status_code == 200 and response.json()["message"] == "Pay item added"
        sql, params = seen["writes"][0]
        assert "|| jsonb_build_array(%s::jsonb)" in sql
        kind, item = params[3]
        assert kind == "json" and UUID(item["id"]) and item["id"] not in (ITEM_1, ITEM_2)
        assert {k: v for k, v in item.items() if k != "id"} == {
            "itemNo": "4.05 A", "budgetCode": "12345", "payQuantity": "12.50", "unit": "C.Y.",
            "description": "Concrete base"}  # the keys the report form saves
        assert params[4:] == [UUID(SWCB_ID), UUID(SWCB_ID), f"payItems[{item['id']}]", "pay_item_add", None,
                              ("json", item), OLIVE["uuid"], "stage1", "pay_item_add"]

    def test_two_added_items_get_different_ids(self):
        with signed_in(OLIVE) as client, backend() as seen:
            client.post(ADD_URL, json=NEW_ITEM)
            client.post(ADD_URL, json=NEW_ITEM)
        first, second = (write[1][3][1]["id"] for write in seen["writes"])
        assert first != second

    def test_text_is_trimmed_and_a_number_quantity_becomes_text(self):
        with signed_in(OLIVE) as client, backend() as seen:
            client.post(ADD_URL, json={**NEW_ITEM, "item_no": " 4.05 A ", "quantity": 12.5, "description": " Base "})
        item = seen["writes"][0][1][3][1]
        assert (item["itemNo"], item["payQuantity"], item["description"]) == ("4.05 A", "12.5", "Base")

    def test_an_item_typed_by_hand_needs_only_a_quantity_and_a_number_or_a_description(self):
        with signed_in(OLIVE) as client, backend() as seen:
            by_number = client.post(ADD_URL, json={"report_id": SWCB_ID, "item_no": "9.99", "quantity": "1"})
            by_text = client.post(ADD_URL, json={"report_id": SWCB_ID, "description": "Extra work", "quantity": "1"})
        assert (by_number.status_code, by_text.status_code) == (200, 200)
        assert seen["writes"][0][1][3][1]["unit"] == "" and seen["writes"][1][1][3][1]["itemNo"] == ""

    def test_an_item_with_neither_is_400(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(ADD_URL, json={"report_id": SWCB_ID, "item_no": " ", "quantity": "1"})
        assert response.status_code == 400
        assert response.json() == {"detail": "A pay item needs an item number or a description"}
        assert seen["writes"] == []

    def test_a_blank_quantity_is_400(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(ADD_URL, json={**NEW_ITEM, "quantity": " "})
        assert response.status_code == 400 and response.json() == {"detail": "A quantity is required"}
        assert seen["writes"] == []

    def test_a_general_takes_pay_items_too(self):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.post(ADD_URL, json={**NEW_ITEM, "report_id": GEN_ID}).status_code == 200
        assert seen["writes"][0][1][4] == UUID(GEN_ID)

    def test_a_report_type_without_a_pay_items_table_is_400(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(ADD_URL, json={**NEW_ITEM, "report_id": MIX_ID})
        assert response.status_code == 400 and response.json() == {"detail": "This kind of report has no pay items"}
        assert seen["writes"] == []

    def test_a_report_that_isnt_in_the_idr_is_404(self):
        with signed_in(OLIVE) as client, backend() as seen:
            response = client.post(ADD_URL, json={**NEW_ITEM, "report_id": IDR_ID})
        assert response.status_code == 404 and seen["writes"] == []

    @pytest.mark.parametrize("body", [{}, {"item_no": "4.05 A", "quantity": "1"}, {"report_id": SWCB_ID, "item_no": "x"},
                                      {**NEW_ITEM, "report_id": "not-a-uuid"}])
    def test_a_malformed_body_is_422(self, body):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.post(ADD_URL, json=body).status_code == 422
        assert seen["writes"] == []


# ---------------------------------------------------------------------------
# Who may edit, and when
# ---------------------------------------------------------------------------

EDIT_ROUTES = [
    ("PATCH", FIELD_URL, field("workforce.foremen", "3")),
    ("POST", revise_url(), {"revised_quantity": "55.00"}),
    ("POST", ADD_URL, NEW_ITEM),
    ("POST", approve_url(), None),
]


class TestWhoMayEdit:
    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_another_reviewer_on_the_project_is_403(self, method, url, body):
        with signed_in(REX) as client, backend(roles=("oe", "re")) as seen:
            response = client.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == NOT_THE_REVIEWER
        assert seen["writes"] == []

    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_the_stage_one_reviewer_cant_edit_once_the_idr_is_with_the_re(self, method, url, body):
        with signed_in(OLIVE) as client, backend(idr=STAGE2_IDR, roles=("oe", "re")) as seen:
            response = client.request(method, url, json=body)
        assert response.status_code == 403 and seen["writes"] == []

    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_the_inspector_cant_use_the_reviewer_routes(self, method, url, body):
        inspector = {**OLIVE, "uuid": STAGE1_IDR["reporter_uuid"]}
        with signed_in(inspector) as client, backend(roles=("inspector",)) as seen:
            response = client.request(method, url, json=body)
        assert response.status_code == 403 and seen["writes"] == []

    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_a_reviewer_whose_role_was_revoked_is_403(self, method, url, body):
        with signed_in(OLIVE) as client, backend(roles=("inspector",)) as seen:
            response = client.request(method, url, json=body)
        assert response.status_code == 403 and seen["writes"] == []

    @pytest.mark.parametrize("status", ["draft", "submitted", "approved", "deleted"])
    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_an_idr_that_isnt_in_review_is_400_for_its_reviewer_and_for_an_admin(self, method, url, body, status):
        # after final approval above all: an approved IDR is not edited, by anyone
        idr = {**STAGE2_IDR, "status": status, "stage1_reviewer_uuid": OLIVE["uuid"], "re_reviewer_uuid": OLIVE["uuid"]}
        with signed_in(OLIVE) as client, backend(idr=idr, roles=("oe", "re")) as seen:
            as_reviewer = client.request(method, url, json=body)
        with signed_in(ADMIN_USER_ROW) as client, backend(idr=idr, roles=()) as seen_admin:
            as_admin = client.request(method, url, json=body)
        for response in (as_reviewer, as_admin):
            assert response.status_code == 400 and response.json() == NOT_IN_REVIEW
        assert seen["writes"] == [] and seen_admin["writes"] == []

    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_an_idr_that_doesnt_exist_is_404(self, method, url, body):
        with signed_in(OLIVE) as client, backend(idr=None) as seen:
            response = client.request(method, url, json=body)
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}
        assert seen["writes"] == []

    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES)
    def test_a_demo_user_gets_nowhere(self, demo_client, method, url, body):
        own = {**STAGE1_IDR, "reporter_uuid": DEMO_USER_ROW["uuid"]}
        with backend(idr=own, roles=("inspector",)) as seen:
            response = demo_client.request(method, url, json=body)
        assert response.status_code == 403 and seen["writes"] == []


# ---------------------------------------------------------------------------
# The auto-generated General
# ---------------------------------------------------------------------------

class TestAutoGeneral:
    @pytest.mark.parametrize("method,url,body", EDIT_ROUTES[:3])  # an approval changes nothing to rebuild from
    def test_it_is_rebuilt_after_an_edit_to_a_report_it_summarises(self, method, url, body):
        with signed_in(OLIVE) as client, backend(reports=(AUTO_GENERAL, SWCB, CONC_MIX)) as seen:
            assert client.request(method, url, json=body).status_code == 200
        seen["regen"].assert_called_once_with(UUID(IDR_ID))

    def test_an_inspectors_own_general_is_never_rebuilt(self):
        with signed_in(OLIVE) as client, backend() as seen:
            assert client.patch(FIELD_URL, json=field("workforce.foremen", "3")).status_code == 200
        seen["regen"].assert_not_called()

    def test_no_general_at_all_means_nothing_to_rebuild_and_none_is_created(self):
        with signed_in(OLIVE) as client, backend(reports=(SWCB, CONC_MIX)) as seen:
            assert client.patch(FIELD_URL, json=field("workforce.foremen", "3")).status_code == 200
        seen["regen"].assert_not_called()

    def test_an_edit_to_an_addendum_doesnt_rebuild_it(self):
        with signed_in(OLIVE) as client, backend(reports=(AUTO_GENERAL, SWCB, CONC_MIX)) as seen:
            response = client.patch(FIELD_URL, json=field("trucks[0].slump", "5", report_id=MIX_ID))
        assert response.status_code == 200
        seen["regen"].assert_not_called()

    def test_a_header_edit_doesnt_rebuild_it(self):
        with signed_in(OLIVE) as client, backend(reports=(AUTO_GENERAL, SWCB)) as seen:
            assert client.patch(FIELD_URL, json={"field_path": "header.weather_am", "new_value": "Rain"}).status_code == 200
        seen["regen"].assert_not_called()

    @pytest.mark.parametrize("method,url,body", [
        ("PATCH", FIELD_URL, field("description", "x", report_id=GEN_ID)),
        ("POST", ADD_URL, {**NEW_ITEM, "report_id": GEN_ID}),
    ])
    def test_the_auto_general_itself_cant_be_edited(self, method, url, body):
        with signed_in(OLIVE) as client, backend(reports=(AUTO_GENERAL, SWCB)) as seen:
            response = client.request(method, url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"].startswith("An auto-generated General can't be edited")
        assert seen["writes"] == []

    def test_a_pay_item_on_the_auto_general_cant_be_revised(self):
        merged = {**AUTO_GENERAL, "report_data": {"payItems": [{**SWCB_DATA["payItems"][0], "id": "merged-1"}]}}
        with signed_in(OLIVE) as client, backend(reports=(merged, SWCB)) as seen:
            response = client.post(revise_url("merged-1"), json={"revised_quantity": "1"})
        assert response.status_code == 400 and seen["writes"] == []


# ---------------------------------------------------------------------------
# GET /v1/idrs/{idr_id} carries the edits
# ---------------------------------------------------------------------------

class TestIdrDetailCarriesEdits:
    url = f"/v1/idrs/{IDR_ID}"

    def test_anyone_who_can_read_the_idr_gets_its_edits_oldest_first(self):
        inspector = {**OLIVE, "uuid": STAGE1_IDR["reporter_uuid"]}
        approved = {**STAGE2_IDR, "status": "approved"}
        with signed_in(inspector) as client, backend(idr=approved, roles=("inspector",)) as seen:
            response = client.get(self.url)
        assert response.status_code == 200
        edits = response.json()["data"]["field_edits"]
        assert [(e["field_path"], e["old_value"], e["new_value"], e["editor_initials"]) for e in edits] == [
            ("workforce.foremen", "2", "3", "OE")]
        assert seen["writes"] == []

    def test_an_idr_nobody_edited_has_an_empty_list(self, admin_client):
        with backend(edits=()):
            assert admin_client.get(self.url).json()["data"]["field_edits"] == []

    def test_an_added_pay_item_comes_back_with_no_old_value_and_the_item_as_the_new_one(self, admin_client):
        item = {"id": "new-1", "itemNo": "4.05 A", "payQuantity": "12.50"}
        added = {**LISTED_EDIT, "field_path": "payItems[new-1]", "edit_type": "pay_item_add", "old_value": None,
                 "new_value": item}
        with backend(edits=(added,)):
            edit = admin_client.get(self.url).json()["data"]["field_edits"][0]
        assert (edit["edit_type"], edit["old_value"], edit["new_value"]) == ("pay_item_add", None, item)

    def test_a_header_edit_has_no_report(self, admin_client):
        header = {**LISTED_EDIT, "report_id": None, "field_path": "header.weather_am", "old_value": "Clear",
                  "new_value": "Rain"}
        with backend(edits=(header,)):
            assert admin_client.get(self.url).json()["data"]["field_edits"][0]["report_id"] is None

    def test_edits_that_cant_be_read_are_500(self, admin_client):
        with patch("api.queries.idrs.run_query", return_value=[STAGE1_IDR]), \
                patch("api.queries.idr_reports.run_query", return_value=[]), \
                patch("api.queries.idr_field_edits.run_query", return_value=None):
            response = admin_client.get(self.url)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to load IDR edits"}

    def test_the_list_endpoint_doesnt_carry_them(self, admin_client):
        with patch("api.queries.idrs.run_query", return_value=[]), \
                patch("api.queries.idr_field_edits.run_query") as edits:
            assert admin_client.get("/v1/idrs/").status_code == 200
        edits.assert_not_called()


# ---------------------------------------------------------------------------
# The path grammar and initials, on their own
# ---------------------------------------------------------------------------

class TestPathGrammar:
    @pytest.mark.parametrize("path,steps", [
        ("description", [("description", None)]),
        ("workforce.foremen", [("workforce", None), ("foremen", None)]),
        ("additionalWorkforce[0].count", [("additionalWorkforce", "0"), ("count", None)]),
        (f"payItems[{ITEM_1}].payQuantity", [("payItems", ITEM_1), ("payQuantity", None)]),
        ("inspectionMatrix.subgradeCompacted.sidewalk", [("inspectionMatrix", None), ("subgradeCompacted", None),
                                                         ("sidewalk", None)]),
    ])
    def test_a_path_splits_into_its_steps(self, path, steps):
        assert parse_report_path(path) == steps

    def test_resolving_gives_the_place_in_the_report_and_the_value_there(self):
        assert resolve_report_path(SWCB_DATA, f"payItems[{ITEM_2}].payQuantity") == (["payItems", "1", "payQuantity"], "29.00")
        assert resolve_report_path(SWCB_DATA, "safetyChecks.plates") == (["safetyChecks", "plates"], None)
        assert resolve_report_path(SWCB_DATA, "additionalWorkforce[0].label") == (["additionalWorkforce", "0", "label"], "Mason")

    def test_a_pay_item_is_found_by_id_wherever_it_sits_in_the_list(self):
        moved = {"payItems": list(reversed(SWCB_DATA["payItems"]))}
        assert resolve_report_path(moved, f"payItems[{ITEM_2}].payQuantity")[0] == ["payItems", "0", "payQuantity"]

    def test_only_the_top_level_pay_items_list_is_looked_up_by_id(self):
        nested = {"section": {"payItems": [{"id": "0", "payQuantity": "1"}, {"id": "x", "payQuantity": "2"}]}}
        assert resolve_report_path(nested, "section.payItems[1].payQuantity") == (["section", "payItems", "1", "payQuantity"], "2")

    @pytest.mark.parametrize("data", [None, [], "text", {"payItems": "not a list"}, {"payItems": ["not an item"]}])
    def test_report_data_of_any_shape_is_a_400_never_a_crash(self, data):
        with pytest.raises(FieldEditError) as raised:
            resolve_report_path(data, f"payItems[{ITEM_1}].payQuantity")
        assert raised.value.status_code == 400


class TestInitials:
    @pytest.mark.parametrize("first,last,initials", [
        ("Olive", "Engineer", "OE"), ("rex", "resident", "RR"), ("Reza", None, "R"), (None, "Khan", "K"),
        (None, None, ""), (" Ada ", " Admin ", "AA"), ("", "", ""),
    ])
    def test_the_first_letter_of_each_name_in_capitals(self, first, last, initials):
        assert _initials(first, last) == initials


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/pay-items/{pay_item_id}/approve
# ---------------------------------------------------------------------------

def my_edit(kind: str, item_id: str, value, stage: str = "stage1", editor: dict = OLIVE, at: datetime = NOW) -> dict:
    """
    Build a listed edit attesting to a pay item of the SWCB report.
    Takes the kind ('approve' | 'revise' | 'add'), the item's id, the quantity (for an add, the item), the stage, the editor and the time.
    Returns the row as list_field_edits returns it.
    """
    path = f"payItems[{item_id}].payQuantity" if kind == "revise" else f"payItems[{item_id}]"
    edit_type = {"approve": "pay_item_approve", "revise": "pay_item_revision", "add": "pay_item_add"}[kind]
    return {**LISTED_EDIT, "field_path": path, "edit_type": edit_type, "old_value": None if kind == "add" else value,
            "new_value": value, "editor_uuid": editor["uuid"], "editor_stage": stage, "edited_at": at,
            "editor_first_name": editor["first_name"], "editor_last_name": editor["last_name"]}


class TestApprovePayItem:
    def test_the_reviewer_approves_an_item_as_it_stands_and_gets_the_idr_back(self):
        with signed_in(OLIVE) as client, backend(edits=()) as seen:
            response = client.post(approve_url(ITEM_2))
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Pay item approved" and body["data"]["idr_id"] == IDR_ID
        sql, params = seen["writes"][0]
        assert "UPDATE icid." not in sql and "jsonb_set" not in sql  # the report is left exactly as it is
        assert params == [UUID(IDR_ID), "stage1_review", OLIVE["uuid"], UUID(SWCB_ID), ["payItems", "1", "payQuantity"],
                          ("json", "29.00"), UUID(SWCB_ID), f"payItems[{ITEM_2}]", "pay_item_approve", ("json", "29.00"),
                          ("json", "29.00"), OLIVE["uuid"], "stage1", "pay_item_approve"]

    def test_the_approval_comes_back_among_the_edits_with_the_reviewers_initials(self):
        approval = my_edit("approve", ITEM_1, "60.00")
        with signed_in(OLIVE) as client, backend(edits=()) as seen:
            seen_before = client.get(f"/v1/idrs/{IDR_ID}").json()["data"]["field_edits"]
        with signed_in(OLIVE) as client, backend(edits=(approval,)):
            edit = client.get(f"/v1/idrs/{IDR_ID}").json()["data"]["field_edits"][0]
        assert seen_before == []
        assert (edit["edit_type"], edit["field_path"], edit["old_value"], edit["new_value"]) == (
            "pay_item_approve", f"payItems[{ITEM_1}]", "60.00", "60.00")
        assert (edit["editor_initials"], edit["editor_stage"]) == ("OE", "stage1")

    def test_approving_again_at_the_same_stage_succeeds_and_logs_nothing(self):
        with signed_in(OLIVE) as client, backend(edits=(my_edit("approve", ITEM_1, "60.00"),)) as seen:
            response = client.post(approve_url(ITEM_1))
        assert response.status_code == 200 and response.json()["message"] == "Pay item approved"
        assert seen["writes"] == []

    @pytest.mark.parametrize("kind,value", [("revise", "60.00"), ("add", {"id": ITEM_1, "payQuantity": "60.00"})])
    def test_an_item_they_already_revised_or_added_needs_no_approval_row(self, kind, value):
        with signed_in(OLIVE) as client, backend(edits=(my_edit(kind, ITEM_1, value),)) as seen:
            assert client.post(approve_url(ITEM_1)).status_code == 200
        assert seen["writes"] == []

    def test_an_approval_of_an_older_quantity_doesnt_stand_in_for_this_one(self):
        with signed_in(OLIVE) as client, backend(edits=(my_edit("approve", ITEM_1, "58.00"),)) as seen:
            assert client.post(approve_url(ITEM_1)).status_code == 200
        assert len(seen["writes"]) == 1 and seen["writes"][0][1][5] == ("json", "60.00")

    def test_another_reviewers_approval_or_their_own_at_another_stage_doesnt_either(self):
        others = (my_edit("approve", ITEM_1, "60.00", editor=REX), my_edit("approve", ITEM_1, "60.00", stage="stage2"))
        with signed_in(OLIVE) as client, backend(edits=others) as seen:
            assert client.post(approve_url(ITEM_1)).status_code == 200
        assert len(seen["writes"]) == 1

    def test_one_from_before_the_stage_was_last_accepted_doesnt_either(self):
        earlier = my_edit("approve", ITEM_1, "60.00", at=datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc))
        accepted = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
        with signed_in(OLIVE) as client, backend(edits=(earlier,), accepted_at=accepted) as seen:
            assert client.post(approve_url(ITEM_1)).status_code == 200
        assert len(seen["writes"]) == 1

    def test_the_re_approves_at_stage_two_under_their_own_stage(self):
        with signed_in(REX) as client, backend(idr=STAGE2_IDR, roles=("re",), edits=(my_edit("approve", ITEM_1, "60.00"),)) as seen:
            assert client.post(approve_url(ITEM_1)).status_code == 200
        sql, params = seen["writes"][0]
        assert "AND re_reviewer_uuid = %s" in sql and params[-3:] == [REX["uuid"], "stage2", "pay_item_approve"]

    def test_an_admin_approves_as_themselves(self, admin_client):
        with backend(roles=(), edits=()) as seen:
            assert admin_client.post(approve_url(ITEM_1)).status_code == 200
        sql, params = seen["writes"][0]
        assert "reviewer_uuid = %s" not in sql and params[-3] == ADMIN_USER_ROW["uuid"]

    def test_an_item_no_report_of_the_idr_holds_is_404(self):
        with signed_in(OLIVE) as client, backend(edits=()) as seen:
            response = client.post(approve_url("no-such-item"))
        assert response.status_code == 404 and response.json() == {"detail": "Pay item not found in this IDR"}
        assert seen["writes"] == []

    def test_an_auto_generated_generals_item_cant_be_approved(self):
        merged = {**AUTO_GENERAL, "report_data": {"payItems": [{**SWCB_DATA["payItems"][0], "id": "merged-1"}]}}
        with signed_in(OLIVE) as client, backend(reports=(merged, SWCB), edits=()) as seen:
            response = client.post(approve_url("merged-1"))
        assert response.status_code == 400 and seen["writes"] == []

    def test_a_quantity_that_changed_meanwhile_is_409(self):
        with signed_in(OLIVE) as client, backend(applied=[], edits=()):
            response = client.post(approve_url(ITEM_1))
        assert response.status_code == 409 and response.json() == CHANGED

    def test_a_failed_statement_is_500(self):
        with signed_in(OLIVE) as client, backend(applied=None, edits=()):
            assert client.post(approve_url(ITEM_1)).status_code == 500

    def test_it_takes_no_body(self):
        with signed_in(OLIVE) as client, backend(edits=()) as seen:
            assert client.post(approve_url(ITEM_1), json={"anything": 1}).status_code == 200
        assert len(seen["writes"]) == 1
