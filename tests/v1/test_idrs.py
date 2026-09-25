from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

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
def patched(idrs=None, projects=None):
    """
    Patch run_query in each query module the IDR endpoints use.
    Takes the return value (or side_effect tuple) for each module's run_query.
    Yields a dict of the mocks keyed by module name.
    """
    def kwargs(value):
        return {"side_effect": value} if isinstance(value, tuple) else {"return_value": value}

    with patch("api.queries.idrs.run_query", **kwargs(idrs)) as i, \
         patch("api.queries.projects.run_query", **kwargs(projects)) as p:
        yield {"idrs": i, "projects": p}


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
