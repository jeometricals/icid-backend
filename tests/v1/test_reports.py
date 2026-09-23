from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

from psycopg.types.json import Jsonb

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the column names in api/queries/reports.py and completed_forms.py.
# ---------------------------------------------------------------------------

REPORT_ID = "3c9a1f2e-5b7d-4e8a-9c1b-2d3e4f5a6b7c"  # reports.report_id
REPORTER_UUID = "7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90"  # users.uuid
NOW = datetime(2026, 9, 22, 15, 30, tzinfo=timezone.utc)
REPORT_DATE = date(2026, 9, 22)  # reports.report_date

MOCK_REPORT_ROW = {
    "report_id": UUID(REPORT_ID),
    "reporter_uuid": UUID(REPORTER_UUID),
    "project_id": "HWS0023",
    "report_date": REPORT_DATE,
    "status": "draft",
    "created_at": NOW,
    "updated_at": NOW,
    "submitted_at": None,
}

MOCK_SUBMITTED_REPORT_ROW = {**MOCK_REPORT_ROW, "status": "submitted", "submitted_at": NOW}

MOCK_PROJECT_ROW = {
    "project_id": "HWS0023",
    "project_name": "Houston St Water Main",
    "project_description": None,
    "registration_code": None,
    "borough": "Manhattan",
    "status": "active",
}

MOCK_ASSIGNMENT_ROW = {"?column?": 1}  # is_user_on_project does SELECT 1

MOCK_USER_ROW = {
    "user_id": UUID(REPORTER_UUID),
    "email": "inspector@example.com",
    "first_name": "Ana",
    "last_name": "Reyes",
    "phone_number": None,
    "employer": "C1",
}

# The General Form exactly as the frontend sends it (camelCase keys).
GENERAL_FORM = {
    "date": "2026-09-22",
    "sheetNo": "1",
    "workActivityStart": "07:00",
    "workActivityEnd": "15:30",
    "inspectorTimeStart": "06:45",
    "inspectorTimeEnd": "16:00",
    "dailyTempLow": "58",
    "dailyTempHigh": "74",
    "weatherAM": "Clear",
    "weatherPM": "Cloudy",
    "description": "Excavated trench along the east curb line.",
    "payItems": [
        {
            "itemNo": "6.01",
            "budgetCode": "BC-100",
            "payQuantity": "40",
            "quantityChk": "40",
            "description": "Trench excavation",
        }
    ],
    "workforce": {"superintendent": "1", "foreman": "1", "operator": "2", "flagger": "2"},
    "equipment": {
        "frontEndLoader": {"model": "CAT 950", "number": "FL-12"},
        "backhoe": {"model": "", "number": ""},
        "truckDump": {"model": "Mack", "number": "TD-4"},
        "excavator": {"model": "", "number": ""},
    },
    "safetyChecks": {
        "plasticBarrels": True,
        "pedestrianBarricades": True,
        "timberCurbs": None,
        "timberBreakawayBarricades": None,
        "generalSafety": True,
        "localEmergencyAccess": True,
        "fencing": False,
        "plates": None,
        "arrowBoard": None,
        "siteCleaned": True,
    },
    "safetyRemarks": "Fencing gap at north end, flagged to foreman.",
    "comments": "",
}

MOCK_UPSERT_ROW = {"completed_form_id": "a1b2c3d4-0000-4000-8000-000000000001", "updated_at": NOW}

MOCK_COMPLETED_FORM_ROW = {**MOCK_UPSERT_ROW, "form_data": GENERAL_FORM}


@contextmanager
def patched(reports=None, completed_forms=None, projects=None, users=None):
    """
    Patch run_query in each query module the reports endpoints use.
    Takes the return value (or side_effect list) for each module's run_query.
    Yields a dict of the four mocks keyed by module name.
    """
    def kwargs(value):
        return {"side_effect": value} if isinstance(value, tuple) else {"return_value": value}

    with patch("api.queries.reports.run_query", **kwargs(reports)) as r, \
         patch("api.queries.completed_forms.run_query", **kwargs(completed_forms)) as cf, \
         patch("api.queries.projects.run_query", **kwargs(projects)) as p, \
         patch("api.queries.users.run_query", **kwargs(users)) as u:
        yield {"reports": r, "completed_forms": cf, "projects": p, "users": u}


# ---------------------------------------------------------------------------
# POST /v1/reports/
# ---------------------------------------------------------------------------

CREATE_BODY = {"project_id": "HWS0023", "reporter_uuid": REPORTER_UUID}

# Creating a report makes two reads through api.queries.projects, in this order:
# get_project_by_id, then is_user_on_project. Side effects supply one per call.
ASSIGNED = ([MOCK_PROJECT_ROW], [MOCK_ASSIGNMENT_ROW])
NOT_ASSIGNED = ([MOCK_PROJECT_ROW], [])


class TestCreateReport:
    def test_returns_201(self, client):
        with patched(reports=[MOCK_REPORT_ROW], projects=ASSIGNED, users=[MOCK_USER_ROW]):
            response = client.post("/v1/reports/", json=CREATE_BODY)
        assert response.status_code == 201

    def test_response_shape(self, client):
        with patched(reports=[MOCK_REPORT_ROW], projects=ASSIGNED, users=[MOCK_USER_ROW]):
            data = client.post("/v1/reports/", json=CREATE_BODY).json()
        assert data["status"] == "success"
        report = data["data"]
        assert report["report_id"] == REPORT_ID
        assert report["reporter_uuid"] == REPORTER_UUID
        assert report["project_id"] == "HWS0023"
        assert report["report_date"] == "2026-09-22"
        assert "created_at" in report
        assert "updated_at" in report

    def test_status_is_draft(self, client):
        with patched(reports=[MOCK_REPORT_ROW], projects=ASSIGNED, users=[MOCK_USER_ROW]):
            report = client.post("/v1/reports/", json=CREATE_BODY).json()["data"]
        assert report["status"] == "draft"

    def test_unknown_project_returns_404(self, client):
        with patched(projects=[], users=[MOCK_USER_ROW]) as mocks:
            response = client.post("/v1/reports/", json=CREATE_BODY)
        assert response.status_code == 404
        mocks["reports"].assert_not_called()

    def test_unknown_reporter_returns_404(self, client):
        with patched(projects=ASSIGNED, users=[]) as mocks:
            response = client.post("/v1/reports/", json=CREATE_BODY)
        assert response.status_code == 404
        mocks["reports"].assert_not_called()

    def test_unassigned_reporter_returns_403(self, client):
        with patched(projects=NOT_ASSIGNED, users=[MOCK_USER_ROW]) as mocks:
            response = client.post("/v1/reports/", json=CREATE_BODY)
        assert response.status_code == 403
        assert response.json()["detail"] == "Reporter is not assigned to this project"
        mocks["reports"].assert_not_called()

    def test_assignment_check_reads_project_users(self, client):
        with patched(reports=[MOCK_REPORT_ROW], projects=ASSIGNED, users=[MOCK_USER_ROW]) as mocks:
            client.post("/v1/reports/", json=CREATE_BODY)
        assert mocks["projects"].call_count == 2
        sql, params = mocks["projects"].call_args.args
        assert "icid.project_users" in sql
        assert params == (UUID(REPORTER_UUID), "HWS0023")

    def test_unknown_project_is_404_not_403(self, client):
        with patched(projects=[], users=[MOCK_USER_ROW]) as mocks:
            response = client.post("/v1/reports/", json=CREATE_BODY)
        assert response.status_code == 404
        assert mocks["projects"].call_count == 1

    def test_report_date_defaults_to_today(self, client):
        with patched(reports=[MOCK_REPORT_ROW], projects=ASSIGNED, users=[MOCK_USER_ROW]) as mocks:
            client.post("/v1/reports/", json=CREATE_BODY)
        sql, params = mocks["reports"].call_args.args
        assert "report_date" in sql
        assert params[2] == date.today()

    def test_report_date_from_body_is_used(self, client):
        body = {**CREATE_BODY, "report_date": "2026-09-20"}
        with patched(reports=[MOCK_REPORT_ROW], projects=ASSIGNED, users=[MOCK_USER_ROW]) as mocks:
            response = client.post("/v1/reports/", json=body)
        assert response.status_code == 201
        assert mocks["reports"].call_args.args[1][2] == date(2026, 9, 20)

    def test_invalid_report_date_returns_422(self, client):
        response = client.post("/v1/reports/", json={**CREATE_BODY, "report_date": "not-a-date"})
        assert response.status_code == 422

    def test_missing_field_returns_422(self, client):
        response = client.post("/v1/reports/", json={"project_id": "HWS0023"})
        assert response.status_code == 422

    def test_non_uuid_reporter_returns_422(self, client):
        response = client.post("/v1/reports/", json={"project_id": "HWS0023", "reporter_uuid": "28"})
        assert response.status_code == 422

    def test_db_failure_returns_500(self, client):
        with patched(reports=None, projects=ASSIGNED, users=[MOCK_USER_ROW]):
            response = client.post("/v1/reports/", json=CREATE_BODY)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# PUT /v1/reports/{report_id}/general
# ---------------------------------------------------------------------------

class TestSaveGeneralForm:
    url = f"/v1/reports/{REPORT_ID}/general"

    def test_returns_200(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_UPSERT_ROW]):
            response = client.put(self.url, json=GENERAL_FORM)
        assert response.status_code == 200

    def test_response_shape(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_UPSERT_ROW]):
            data = client.put(self.url, json=GENERAL_FORM).json()
        assert data["status"] == "success"
        assert data["data"]["report_id"] == REPORT_ID
        assert data["data"]["completed_form_id"] == MOCK_UPSERT_ROW["completed_form_id"]
        assert "saved_at" in data["data"]

    def test_stores_form_as_jsonb_with_camelcase_keys(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_UPSERT_ROW]) as mocks:
            client.put(self.url, json=GENERAL_FORM)
        sql, params = mocks["completed_forms"].call_args.args
        assert params[0] == UUID(REPORT_ID)
        assert params[1] == "GENERAL"
        assert isinstance(params[2], Jsonb)
        assert params[2].obj == GENERAL_FORM

    def test_upsert_sets_updated_at(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_UPSERT_ROW]) as mocks:
            client.put(self.url, json=GENERAL_FORM)
        sql = mocks["completed_forms"].call_args.args[0]
        assert "ON CONFLICT (report_id, form_template_id)" in sql
        assert "updated_at = now()" in sql

    def test_touches_report_updated_at(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_UPSERT_ROW]) as mocks:
            client.put(self.url, json=GENERAL_FORM)
        assert mocks["reports"].call_count == 2
        sql, params = mocks["reports"].call_args.args
        assert "UPDATE icid.reports" in sql
        assert "updated_at = now()" in sql
        assert params == (UUID(REPORT_ID),)

    def test_empty_body_fills_defaults(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_UPSERT_ROW]) as mocks:
            response = client.put(self.url, json={})
        assert response.status_code == 200
        stored = mocks["completed_forms"].call_args.args[1][2].obj
        assert stored["payItems"] == []
        assert stored["weatherAM"] == ""
        assert stored["safetyChecks"]["siteCleaned"] is None

    def test_missing_report_returns_404(self, client):
        with patched(reports=[]) as mocks:
            response = client.put(self.url, json=GENERAL_FORM)
        assert response.status_code == 404
        mocks["completed_forms"].assert_not_called()

    def test_submitted_report_returns_409(self, client):
        with patched(reports=[MOCK_SUBMITTED_REPORT_ROW]) as mocks:
            response = client.put(self.url, json=GENERAL_FORM)
        assert response.status_code == 409
        mocks["completed_forms"].assert_not_called()

    def test_unknown_field_returns_422(self, client):
        response = client.put(self.url, json={**GENERAL_FORM, "notAField": "x"})
        assert response.status_code == 422

    def test_wrong_type_returns_422(self, client):
        response = client.put(self.url, json={**GENERAL_FORM, "payItems": "not a list"})
        assert response.status_code == 422

    def test_non_uuid_report_id_returns_422(self, client):
        response = client.put("/v1/reports/R1/general", json=GENERAL_FORM)
        assert response.status_code == 422

    def test_upsert_failure_returns_500(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=None):
            response = client.put(self.url, json=GENERAL_FORM)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/reports/{report_id}
# ---------------------------------------------------------------------------

class TestGetReport:
    url = f"/v1/reports/{REPORT_ID}"

    def test_returns_200(self, client):
        with patched(reports=[MOCK_REPORT_ROW], completed_forms=[MOCK_COMPLETED_FORM_ROW]):
            response = client.get(self.url)
        assert response.status_code == 200

    def test_includes_general_form_as_saved(self, client):
        with patched(reports=[MOCK_REPORT_ROW], completed_forms=[MOCK_COMPLETED_FORM_ROW]):
            data = client.get(self.url).json()
        assert data["status"] == "success"
        assert data["data"]["report_id"] == REPORT_ID
        assert data["data"]["status"] == "draft"
        assert data["data"]["general_form"] == GENERAL_FORM

    def test_general_form_null_when_not_saved(self, client):
        with patched(reports=[MOCK_REPORT_ROW], completed_forms=[]):
            data = client.get(self.url).json()
        assert data["data"]["general_form"] is None

    def test_missing_report_returns_404(self, client):
        with patched(reports=[]):
            response = client.get(self.url)
        assert response.status_code == 404

    def test_non_uuid_report_id_returns_422(self, client):
        response = client.get("/v1/reports/R1")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /v1/reports/{report_id}/submit
# ---------------------------------------------------------------------------

# Submitting reads the report, then runs the conditional UPDATE, both through
# api.queries.reports. Side effects supply one result per call.
DRAFT_THEN_SUBMITTED = ([MOCK_REPORT_ROW], [MOCK_SUBMITTED_REPORT_ROW])


class TestSubmitReport:
    url = f"/v1/reports/{REPORT_ID}/submit"

    def test_returns_200_with_submitted_report(self, client):
        with patched(reports=DRAFT_THEN_SUBMITTED, completed_forms=[MOCK_COMPLETED_FORM_ROW]):
            response = client.post(self.url)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["report_id"] == REPORT_ID
        assert data["data"]["status"] == "submitted"
        assert data["data"]["submitted_at"] == "2026-09-22T15:30:00Z"

    def test_update_sets_status_and_submitted_at_only_on_drafts(self, client):
        with patched(reports=DRAFT_THEN_SUBMITTED, completed_forms=[MOCK_COMPLETED_FORM_ROW]) as mocks:
            client.post(self.url)
        sql, params = mocks["reports"].call_args.args
        assert "UPDATE icid.reports" in sql
        assert "status = 'submitted'" in sql
        assert "submitted_at = now()" in sql
        assert "status = 'draft'" in sql
        assert params == (UUID(REPORT_ID),)

    def test_missing_report_returns_404(self, client):
        with patched(reports=[]) as mocks:
            response = client.post(self.url)
        assert response.status_code == 404
        assert mocks["reports"].call_count == 1

    def test_already_submitted_returns_409(self, client):
        with patched(reports=[MOCK_SUBMITTED_REPORT_ROW]) as mocks:
            response = client.post(self.url)
        assert response.status_code == 409
        assert mocks["reports"].call_count == 1

    def test_unsaved_general_form_returns_409(self, client):
        with patched(reports=[MOCK_REPORT_ROW], completed_forms=[]) as mocks:
            response = client.post(self.url)
        assert response.status_code == 409
        assert mocks["reports"].call_count == 1

    def test_non_uuid_report_id_returns_422(self, client):
        response = client.post("/v1/reports/R1/submit")
        assert response.status_code == 422

    def test_update_failure_returns_500(self, client):
        with patched(reports=([MOCK_REPORT_ROW], None), completed_forms=[MOCK_COMPLETED_FORM_ROW]):
            response = client.post(self.url)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/reports/?project_id=...
# ---------------------------------------------------------------------------

MOCK_LIST_ROW = {**MOCK_REPORT_ROW, "description_preview": "Excavated trench along the east curb line."}
MOCK_LIST_ROW_NO_FORM = {
    **MOCK_REPORT_ROW,
    "report_id": UUID("5d6e7f80-1a2b-4c3d-8e9f-0a1b2c3d4e5f"),
    "description_preview": None,
}


class TestListReports:
    url = "/v1/reports/"

    def test_returns_200(self, client):
        with patched(reports=[MOCK_LIST_ROW]):
            response = client.get(self.url, params={"project_id": "HWS0023"})
        assert response.status_code == 200

    def test_response_shape(self, client):
        with patched(reports=[MOCK_LIST_ROW, MOCK_LIST_ROW_NO_FORM]):
            data = client.get(self.url, params={"project_id": "HWS0023"}).json()
        assert data["status"] == "success"
        assert len(data["data"]) == 2
        first = data["data"][0]
        assert first["report_id"] == REPORT_ID
        assert first["status"] == "draft"
        assert first["report_date"] == "2026-09-22"
        assert first["description_preview"] == "Excavated trench along the east curb line."

    def test_preview_null_when_form_never_saved(self, client):
        with patched(reports=[MOCK_LIST_ROW_NO_FORM]):
            data = client.get(self.url, params={"project_id": "HWS0023"}).json()
        assert data["data"][0]["description_preview"] is None

    def test_no_matches_returns_empty_list(self, client):
        with patched(reports=[]):
            response = client.get(self.url, params={"project_id": "HWS0023", "status": "draft"})
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_all_filters_reach_the_query(self, client):
        with patched(reports=[]) as mocks:
            client.get(self.url, params={"project_id": "HWS0023", "reporter_uuid": REPORTER_UUID, "status": "draft"})
        sql, params = mocks["reports"].call_args.args
        assert "r.reporter_uuid = %s" in sql
        assert "r.status = %s" in sql
        assert params == ("GENERAL", "HWS0023", UUID(REPORTER_UUID), "draft")

    def test_optional_filters_omitted_when_not_given(self, client):
        with patched(reports=[]) as mocks:
            client.get(self.url, params={"project_id": "HWS0023"})
        sql, params = mocks["reports"].call_args.args
        assert "r.reporter_uuid = %s" not in sql
        assert "r.status = %s" not in sql
        assert params == ("GENERAL", "HWS0023")

    def test_orders_by_most_recently_edited(self, client):
        with patched(reports=[]) as mocks:
            client.get(self.url, params={"project_id": "HWS0023"})
        sql, _ = mocks["reports"].call_args.args
        assert "ORDER BY r.updated_at DESC" in sql

    def test_missing_project_id_returns_422(self, client):
        response = client.get(self.url)
        assert response.status_code == 422

    def test_invalid_status_returns_422(self, client):
        response = client.get(self.url, params={"project_id": "HWS0023", "status": "approved"})
        assert response.status_code == 422

    def test_non_uuid_reporter_returns_422(self, client):
        response = client.get(self.url, params={"project_id": "HWS0023", "reporter_uuid": "28"})
        assert response.status_code == 422

    def test_query_failure_returns_500(self, client):
        with patched(reports=None):
            response = client.get(self.url, params={"project_id": "HWS0023"})
        assert response.status_code == 500
