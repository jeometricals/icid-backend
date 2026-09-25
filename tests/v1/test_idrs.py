from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

from api.schemas.idr_report import ADDENDUM_TYPES, ReportType

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the column names in api/queries/idrs.py and projects.py.
# ---------------------------------------------------------------------------

IDR_ID = "9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e"  # idrs.idr_id
EXISTING_IDR_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
REPORTER_UUID = "7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90"  # users.uuid
NOW = datetime(2026, 9, 25, 15, 30, tzinfo=timezone.utc)
REPORT_DATE = date(2026, 9, 25)  # idrs.report_date

MOCK_IDR_ROW = {
    "idr_id": UUID(IDR_ID),
    "project_id": "HWS0023",
    "reporter_uuid": UUID(REPORTER_UUID),
    "report_date": REPORT_DATE,
    "work_start_time": None,
    "work_end_time": None,
    "inspector_start_time": None,
    "inspector_end_time": None,
    "temp_low": None,
    "temp_high": None,
    "weather_am": None,
    "weather_pm": None,
    "total_pages": None,
    "status": "draft",
    "submitted_at": None,
    "created_at": NOW,
    "updated_at": NOW,
}

MOCK_PROJECT_ROW = {
    "project_id": "HWS0023",
    "project_name": "Houston St Water Main",
    "project_description": None,
    "registration_code": None,
    "borough": "Manhattan",
    "status": "active",
}

MOCK_ASSIGNMENT_ROW = {"?column?": 1}  # is_user_on_project does SELECT 1


@contextmanager
def patched(idrs=None, idr_reports=None, projects=None):
    """
    Patch run_query in each query module the IDR endpoints use.
    Takes the return value (or side_effect tuple) for each module's run_query.
    Yields a dict of the mocks keyed by module name.
    """
    def kwargs(value):
        return {"side_effect": value} if isinstance(value, tuple) else {"return_value": value}

    with patch("api.queries.idrs.run_query", **kwargs(idrs)) as i, \
         patch("api.queries.idr_reports.run_query", **kwargs(idr_reports)) as ir, \
         patch("api.queries.projects.run_query", **kwargs(projects)) as p:
        yield {"idrs": i, "idr_reports": ir, "projects": p}


# ---------------------------------------------------------------------------
# POST /v1/idrs/
# ---------------------------------------------------------------------------

CREATE_BODY = {"project_id": "HWS0023", "reporter_uuid": REPORTER_UUID, "report_date": "2026-09-25"}

# Creating an IDR makes two reads through api.queries.projects, in this order:
# get_project_by_id, then is_user_on_project. Side effects supply one per call.
ASSIGNED = ([MOCK_PROJECT_ROW], [MOCK_ASSIGNMENT_ROW])
NOT_ASSIGNED = ([MOCK_PROJECT_ROW], [])

# A collision: the insert returns no row, then the lookup finds the existing IDR.
COLLISION = ([], [{"idr_id": UUID(EXISTING_IDR_ID)}])


class TestCreateIdr:
    url = "/v1/idrs/"

    def test_returns_201(self, client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED):
            response = client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 201

    def test_response_shape(self, client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED):
            data = client.post(self.url, json=CREATE_BODY).json()
        assert data["status"] == "success"
        idr = data["data"]
        assert idr["idr_id"] == IDR_ID
        assert idr["project_id"] == "HWS0023"
        assert idr["reporter_uuid"] == REPORTER_UUID
        assert idr["report_date"] == "2026-09-25"
        assert "created_at" in idr
        assert "updated_at" in idr

    def test_new_idr_is_draft_with_empty_header(self, client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED):
            idr = client.post(self.url, json=CREATE_BODY).json()["data"]
        assert idr["status"] == "draft"
        assert idr["submitted_at"] is None
        assert idr["total_pages"] is None
        assert idr["work_start_time"] is None
        assert idr["temp_low"] is None
        assert idr["weather_am"] is None

    def test_insert_targets_idrs_with_body_values(self, client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
            client.post(self.url, json=CREATE_BODY)
        sql, params = mocks["idrs"].call_args.args
        assert "INSERT INTO icid.idrs" in sql
        assert "ON CONFLICT (project_id, reporter_uuid, report_date) DO NOTHING" in sql
        assert params == ("HWS0023", UUID(REPORTER_UUID), REPORT_DATE)

    def test_existing_idr_for_day_returns_409_with_its_id(self, client):
        with patched(idrs=COLLISION, projects=ASSIGNED):
            response = client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 409
        assert response.json() == {
            "detail": "IDR already exists for this project and date",
            "existing_idr_id": EXISTING_IDR_ID,
        }

    def test_collision_lookup_uses_same_project_reporter_date(self, client):
        with patched(idrs=COLLISION, projects=ASSIGNED) as mocks:
            client.post(self.url, json=CREATE_BODY)
        assert mocks["idrs"].call_count == 2
        sql, params = mocks["idrs"].call_args.args
        assert "FROM icid.idrs" in sql
        assert params == ("HWS0023", UUID(REPORTER_UUID), REPORT_DATE)

    def test_unknown_project_returns_404(self, client):
        with patched(projects=[]) as mocks:
            response = client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"
        assert mocks["projects"].call_count == 1
        mocks["idrs"].assert_not_called()

    def test_unassigned_reporter_returns_403(self, client):
        with patched(projects=NOT_ASSIGNED) as mocks:
            response = client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 403
        assert response.json()["detail"] == "Reporter is not assigned to this project"
        mocks["idrs"].assert_not_called()

    def test_assignment_check_reads_project_users(self, client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
            client.post(self.url, json=CREATE_BODY)
        assert mocks["projects"].call_count == 2
        sql, params = mocks["projects"].call_args.args
        assert "icid.project_users" in sql
        assert params == (UUID(REPORTER_UUID), "HWS0023")

    def test_provided_report_date_is_used(self, client):
        body = {**CREATE_BODY, "report_date": "2026-09-20"}
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
            response = client.post(self.url, json=body)
        assert response.status_code == 201
        assert mocks["idrs"].call_args.args[1][2] == date(2026, 9, 20)

    def test_missing_report_date_returns_422(self, client):
        body = {"project_id": "HWS0023", "reporter_uuid": REPORTER_UUID}
        with patched() as mocks:
            response = client.post(self.url, json=body)
        assert response.status_code == 422
        mocks["projects"].assert_not_called()
        mocks["idrs"].assert_not_called()

    def test_invalid_report_date_returns_422(self, client):
        response = client.post(self.url, json={**CREATE_BODY, "report_date": "not-a-date"})
        assert response.status_code == 422

    def test_missing_project_id_returns_422(self, client):
        body = {"reporter_uuid": REPORTER_UUID, "report_date": "2026-09-25"}
        response = client.post(self.url, json=body)
        assert response.status_code == 422

    def test_non_uuid_reporter_returns_422(self, client):
        response = client.post(self.url, json={**CREATE_BODY, "reporter_uuid": "28"})
        assert response.status_code == 422

    def test_insert_failure_returns_500(self, client):
        with patched(idrs=None, projects=ASSIGNED):
            response = client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 500

    def test_collision_without_existing_row_returns_500(self, client):
        with patched(idrs=([], []), projects=ASSIGNED):
            response = client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/idrs/{idr_id}
# ---------------------------------------------------------------------------

GEN_REPORT_ID = "c4d5e6f7-a8b9-4c0d-9e1f-2a3b4c5d6e7f"  # idr_reports.report_id
ADDENDUM_REPORT_ID = "d5e6f7a8-b9c0-4d1e-8f2a-3b4c5d6e7f80"

# report_data exactly as stored in JSONB — nested, camelCase, shape owned by the frontend.
GEN_REPORT_DATA = {
    "description": "Excavated trench along the east curb line.",
    "payItems": [{"itemNo": "6.01", "payQuantity": "40"}],
    "workforce": {"foreman": "1", "operator": "2"},
    "safetyChecks": {"fencing": False, "plates": None},
}

MOCK_GEN_REPORT_ROW = {
    "report_id": UUID(GEN_REPORT_ID),
    "idr_id": UUID(IDR_ID),
    "report_type": "GEN",
    "is_addendum": False,
    "parent_report_id": None,
    "page_number": None,
    "report_data": GEN_REPORT_DATA,
    "created_at": NOW,
    "updated_at": NOW,
}

MOCK_ADDENDUM_ROW = {
    **MOCK_GEN_REPORT_ROW,
    "report_id": UUID(ADDENDUM_REPORT_ID),
    "report_type": "SKETCH",
    "is_addendum": True,
    "parent_report_id": UUID(GEN_REPORT_ID),
    "report_data": {"anyShape": ["the", "frontend", "wants"], "nested": {"x": 1}},
}

MOCK_SUBMITTED_IDR_ROW = {**MOCK_IDR_ROW, "status": "submitted", "submitted_at": NOW, "total_pages": 2}


class TestGetIdr:
    url = f"/v1/idrs/{IDR_ID}"

    def test_returns_200_with_idr_fields(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]):
            response = client.get(self.url)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        idr = data["data"]
        assert idr["idr_id"] == IDR_ID
        assert idr["project_id"] == "HWS0023"
        assert idr["reporter_uuid"] == REPORTER_UUID
        assert idr["report_date"] == "2026-09-25"
        assert idr["status"] == "draft"

    def test_reports_nested_with_report_data_as_stored(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]):
            reports = client.get(self.url).json()["data"]["reports"]
        assert len(reports) == 1
        report = reports[0]
        assert report["report_id"] == GEN_REPORT_ID
        assert report["idr_id"] == IDR_ID
        assert report["report_type"] == "GEN"
        assert report["is_addendum"] is False
        assert report["parent_report_id"] is None
        assert report["report_data"] == GEN_REPORT_DATA

    def test_page_number_present_when_null(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]):
            report = client.get(self.url).json()["data"]["reports"][0]
        assert "page_number" in report
        assert report["page_number"] is None

    def test_no_reports_returns_empty_list(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            idr = client.get(self.url).json()["data"]
        assert idr["reports"] == []

    def test_addendum_with_arbitrary_report_data(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW, MOCK_ADDENDUM_ROW]):
            reports = client.get(self.url).json()["data"]["reports"]
        addendum = reports[1]
        assert addendum["is_addendum"] is True
        assert addendum["parent_report_id"] == GEN_REPORT_ID
        assert addendum["report_data"] == MOCK_ADDENDUM_ROW["report_data"]

    def test_submitted_idr_includes_submit_fields_and_page_numbers(self, client):
        pages = [
            {**MOCK_GEN_REPORT_ROW, "page_number": 1},
            {**MOCK_ADDENDUM_ROW, "page_number": 2},
        ]
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW], idr_reports=pages):
            idr = client.get(self.url).json()["data"]
        assert idr["status"] == "submitted"
        assert idr["submitted_at"] == "2026-09-25T15:30:00Z"
        assert idr["total_pages"] == 2
        assert [r["page_number"] for r in idr["reports"]] == [1, 2]

    def test_queries_read_both_tables_by_idr_id(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            client.get(self.url)
        idr_sql, idr_params = mocks["idrs"].call_args.args
        assert "FROM icid.idrs" in idr_sql
        assert idr_params == (UUID(IDR_ID),)
        reports_sql, reports_params = mocks["idr_reports"].call_args.args
        assert "FROM icid.idr_reports" in reports_sql
        assert reports_params == (UUID(IDR_ID),)

    def test_reports_ordered_by_page_then_creation(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            client.get(self.url)
        sql = mocks["idr_reports"].call_args.args[0]
        assert "ORDER BY page_number NULLS LAST, created_at" in sql

    def test_missing_idr_returns_404(self, client):
        with patched(idrs=[]) as mocks:
            response = client.get(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_non_uuid_idr_id_returns_422(self, client):
        response = client.get("/v1/idrs/IDR1")
        assert response.status_code == 422

    def test_reports_query_failure_returns_500(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=None):
            response = client.get(self.url)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/reports
# ---------------------------------------------------------------------------

NEW_REPORT_ID = "e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091"

MOCK_NEW_SWR_ROW = {
    **MOCK_GEN_REPORT_ROW,
    "report_id": UUID(NEW_REPORT_ID),
    "report_type": "SWR",
    "report_data": {},
}

MOCK_NEW_SKETCH_ROW = {
    **MOCK_NEW_SWR_ROW,
    "report_type": "SKETCH",
    "is_addendum": True,
    "parent_report_id": UUID(GEN_REPORT_ID),
}

# api.queries.idrs is read for the IDR, then written by touch_idr (no result set).
DRAFT_IDR_THEN_TOUCH = ([MOCK_IDR_ROW], None)

# A second General: the insert returns no row, then the lookup finds the existing one.
GEN_COLLISION = ([], [{"report_id": UUID(GEN_REPORT_ID)}])


class TestAddReport:
    url = f"/v1/idrs/{IDR_ID}/reports"

    def test_returns_201_with_new_report(self, client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]):
            response = client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        report = data["data"]
        assert report["report_id"] == NEW_REPORT_ID
        assert report["idr_id"] == IDR_ID
        assert report["report_type"] == "SWR"
        assert report["report_data"] == {}
        assert report["page_number"] is None

    def test_insert_defaults_to_main_report_without_parent(self, client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            client.post(self.url, json={"report_type": "SWR"})
        assert mocks["idr_reports"].call_count == 1
        sql, params = mocks["idr_reports"].call_args.args
        assert "INSERT INTO icid.idr_reports" in sql
        assert params == (UUID(IDR_ID), "SWR", False, None)

    def test_insert_guards_second_general_with_on_conflict(self, client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            client.post(self.url, json={"report_type": "SWR"})
        sql = mocks["idr_reports"].call_args.args[0]
        assert "ON CONFLICT (idr_id) WHERE report_type = 'GEN' AND is_addendum = false DO NOTHING" in sql

    def test_addendum_types_default_to_not_addendum(self, client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            client.post(self.url, json={"report_type": "SKETCH"})
        assert mocks["idr_reports"].call_args.args[1][2] is False

    def test_touches_idr_updated_at(self, client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            client.post(self.url, json={"report_type": "SWR"})
        assert mocks["idrs"].call_count == 2
        sql, params = mocks["idrs"].call_args.args
        assert "UPDATE icid.idrs" in sql
        assert "updated_at = now()" in sql
        assert params == (UUID(IDR_ID),)

    def test_standalone_addendum_without_parent_is_allowed(self, client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            response = client.post(self.url, json={"report_type": "FIELD_MEMO", "is_addendum": True})
        assert response.status_code == 201
        assert mocks["idr_reports"].call_args.args[1] == (UUID(IDR_ID), "FIELD_MEMO", True, None)

    def test_addendum_with_parent_in_same_idr(self, client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": GEN_REPORT_ID}
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=([MOCK_GEN_REPORT_ROW], [MOCK_NEW_SKETCH_ROW])) as mocks:
            response = client.post(self.url, json=body)
        assert response.status_code == 201
        assert response.json()["data"]["parent_report_id"] == GEN_REPORT_ID
        parent_sql, parent_params = mocks["idr_reports"].call_args_list[0].args
        assert "WHERE idr_id = %s AND report_id = %s" in parent_sql
        assert parent_params == (UUID(IDR_ID), UUID(GEN_REPORT_ID))
        assert mocks["idr_reports"].call_args.args[1] == (UUID(IDR_ID), "SKETCH", True, UUID(GEN_REPORT_ID))

    def test_second_general_returns_409_with_existing_id(self, client):
        with patched(idrs=([MOCK_IDR_ROW],), idr_reports=GEN_COLLISION) as mocks:
            response = client.post(self.url, json={"report_type": "GEN"})
        assert response.status_code == 409
        assert response.json() == {
            "detail": "IDR already has a General report",
            "existing_report_id": GEN_REPORT_ID,
        }
        lookup_sql, lookup_params = mocks["idr_reports"].call_args.args
        assert "report_type = 'GEN' AND is_addendum = false" in lookup_sql
        assert lookup_params == (UUID(IDR_ID),)
        assert mocks["idrs"].call_count == 1  # no touch on conflict

    def test_missing_idr_returns_404(self, client):
        with patched(idrs=[]) as mocks:
            response = client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_submitted_idr_returns_409(self, client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW]) as mocks:
            response = client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be edited"
        mocks["idr_reports"].assert_not_called()

    def test_parent_on_main_report_returns_400(self, client):
        body = {"report_type": "SWR", "parent_report_id": GEN_REPORT_ID}
        with patched(idrs=[MOCK_IDR_ROW]) as mocks:
            response = client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "Only addendums can have a parent report"
        mocks["idr_reports"].assert_not_called()

    def test_parent_not_in_this_idr_returns_400(self, client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": GEN_REPORT_ID}
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "Parent report not found in this IDR"
        assert mocks["idr_reports"].call_count == 1  # lookup only, no insert

    def test_addendum_parent_returns_400(self, client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": ADDENDUM_REPORT_ID}
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_ADDENDUM_ROW]) as mocks:
            response = client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "An addendum's parent must be a main report, not another addendum"
        assert mocks["idr_reports"].call_count == 1

    def test_unknown_report_type_returns_422(self, client):
        with patched() as mocks:
            response = client.post(self.url, json={"report_type": "WM"})
        assert response.status_code == 422
        assert "GEN" in str(response.json()["detail"])
        mocks["idrs"].assert_not_called()

    def test_missing_report_type_returns_422(self, client):
        response = client.post(self.url, json={"is_addendum": True})
        assert response.status_code == 422

    def test_non_uuid_idr_id_returns_422(self, client):
        response = client.post("/v1/idrs/IDR1/reports", json={"report_type": "SWR"})
        assert response.status_code == 422

    def test_non_uuid_parent_returns_422(self, client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": "R1"}
        response = client.post(self.url, json=body)
        assert response.status_code == 422

    def test_insert_failure_returns_500(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=None):
            response = client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 500

    def test_collision_without_existing_general_returns_500(self, client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=([], [])):
            response = client.post(self.url, json={"report_type": "GEN"})
        assert response.status_code == 500


class TestReportTypeEnum:
    def test_has_21_types(self):
        assert len(ReportType) == 21

    def test_addendum_types_are_report_types_and_exclude_dsp(self):
        assert ADDENDUM_TYPES <= set(ReportType)
        assert ReportType.DSP not in ADDENDUM_TYPES
        assert len(ADDENDUM_TYPES) == 6
