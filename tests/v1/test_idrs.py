import logging
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import ANY, MagicMock, call, patch
from uuid import UUID

from psycopg.types.json import Jsonb

from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW
from api.services.signatures import SignatureStorageError
from api.schemas.idr_report import ADDENDUM_TYPES, ReportType, TYPE_LABELS, label_for
from api.services.auto_general import DESCRIPTION_FOOTER, build_auto_general_data, regenerate_auto_general

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
    "has_dismissed_auto_general": False,
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


# Where submit's copy of the signer's signature lands (the real path ends in a random name)
SIGNATURE_COPY = f"idrs/{IDR_ID}/inspector_0123456789abcdef0123456789abcdef.png"


@contextmanager
def patched(idrs=None, idr_reports=None, projects=None):
    """
    Patch run_query in each query module the IDR endpoints use, plus the auto-General and attachment-Storage hooks.
    Takes the return value (or side_effect tuple) for each module's run_query.
    Yields a dict of the mocks keyed by module name, with "regen", "dismiss" and "storage" for the hooks, and
    "signature" for submit's copy of the signer's signature (it returns SIGNATURE_COPY).
    """
    def kwargs(value):
        return {"side_effect": value} if isinstance(value, tuple) else {"return_value": value}

    with patch("api.queries.idrs.run_query", **kwargs(idrs)) as i, \
         patch("api.queries.idr_reports.run_query", **kwargs(idr_reports)) as ir, \
         patch("api.queries.projects.run_query", **kwargs(projects)) as p, \
         patch("api.v1.idrs.regenerate_auto_general") as regen, \
         patch("api.v1.idrs.set_dismissed_auto_general") as dismiss, \
         patch("api.v1.idrs.delete_all_storage_files_for_report") as storage, \
         patch("api.v1.idrs.snapshot_signature_for_idr", return_value=SIGNATURE_COPY) as signature:
        yield {"idrs": i, "idr_reports": ir, "projects": p, "regen": regen, "dismiss": dismiss, "storage": storage,
               "signature": signature}


# ---------------------------------------------------------------------------
# POST /v1/idrs/
# ---------------------------------------------------------------------------

# The reporter is the signed-in user (ADMIN_USER_ROW), not part of the body
CREATE_BODY = {"project_id": "HWS0023", "report_date": "2026-09-25"}

# Creating an IDR makes two reads through api.queries.projects, in this order:
# get_project_by_id, then is_user_on_project. Side effects supply one per call.
ASSIGNED = ([MOCK_PROJECT_ROW], [MOCK_ASSIGNMENT_ROW])
NOT_ASSIGNED = ([MOCK_PROJECT_ROW], [])

# A collision: the insert returns no row, then the lookup finds the existing IDR.
COLLISION = ([], [{"idr_id": UUID(EXISTING_IDR_ID)}])


class TestCreateIdr:
    url = "/v1/idrs/"

    def test_returns_201(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED):
            response = admin_client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 201

    def test_response_shape(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED):
            data = admin_client.post(self.url, json=CREATE_BODY).json()
        assert data["status"] == "success"
        idr = data["data"]
        assert idr["idr_id"] == IDR_ID
        assert idr["project_id"] == "HWS0023"
        assert idr["reporter_uuid"] == REPORTER_UUID
        assert idr["report_date"] == "2026-09-25"
        assert "created_at" in idr
        assert "updated_at" in idr

    def test_new_idr_is_draft_with_empty_header(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED):
            idr = admin_client.post(self.url, json=CREATE_BODY).json()["data"]
        assert idr["status"] == "draft"
        assert idr["submitted_at"] is None
        assert idr["total_pages"] is None
        assert idr["work_start_time"] is None
        assert idr["temp_low"] is None
        assert idr["weather_am"] is None

    def test_insert_targets_idrs_with_body_values(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
            admin_client.post(self.url, json=CREATE_BODY)
        sql, params = mocks["idrs"].call_args.args
        assert "INSERT INTO icid.idrs" in sql
        assert "ON CONFLICT DO NOTHING" in sql  # no target: the per-day index is partial since migration 017
        assert params == ("HWS0023", ADMIN_USER_ROW["uuid"], REPORT_DATE)

    def test_existing_idr_for_day_returns_409_with_its_id(self, admin_client):
        with patched(idrs=COLLISION, projects=ASSIGNED):
            response = admin_client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 409
        assert response.json() == {
            "detail": "IDR already exists for this project and date",
            "existing_idr_id": EXISTING_IDR_ID,
        }

    def test_collision_lookup_uses_same_project_reporter_date(self, admin_client):
        with patched(idrs=COLLISION, projects=ASSIGNED) as mocks:
            admin_client.post(self.url, json=CREATE_BODY)
        assert mocks["idrs"].call_count == 2
        sql, params = mocks["idrs"].call_args.args
        assert "FROM icid.idrs" in sql
        assert params == ("HWS0023", ADMIN_USER_ROW["uuid"], REPORT_DATE)

    def test_unknown_project_returns_404(self, admin_client):
        with patched(projects=[]) as mocks:
            response = admin_client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "Project not found"
        assert mocks["projects"].call_count == 1
        mocks["idrs"].assert_not_called()

    def test_unassigned_reporter_returns_403(self, admin_client):
        with patched(projects=NOT_ASSIGNED) as mocks:
            response = admin_client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 403
        assert response.json()["detail"] == "Reporter is not assigned to this project"
        mocks["idrs"].assert_not_called()

    def test_assignment_check_reads_project_users(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
            admin_client.post(self.url, json=CREATE_BODY)
        assert mocks["projects"].call_count == 2
        sql, params = mocks["projects"].call_args.args
        assert "icid.project_users" in sql
        assert params == (ADMIN_USER_ROW["uuid"], "HWS0023")

    def test_provided_report_date_is_used(self, admin_client):
        body = {**CREATE_BODY, "report_date": "2026-09-20"}
        with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
            response = admin_client.post(self.url, json=body)
        assert response.status_code == 201
        assert mocks["idrs"].call_args.args[1][2] == date(2026, 9, 20)

    def test_missing_report_date_returns_422(self, admin_client):
        body = {"project_id": "HWS0023"}
        with patched() as mocks:
            response = admin_client.post(self.url, json=body)
        assert response.status_code == 422
        mocks["projects"].assert_not_called()
        mocks["idrs"].assert_not_called()

    def test_invalid_report_date_returns_422(self, admin_client):
        response = admin_client.post(self.url, json={**CREATE_BODY, "report_date": "not-a-date"})
        assert response.status_code == 422

    def test_missing_project_id_returns_422(self, admin_client):
        body = {"report_date": "2026-09-25"}
        response = admin_client.post(self.url, json=body)
        assert response.status_code == 422

    def test_a_reporter_uuid_in_the_body_is_ignored(self, admin_client):
        # the reporter is always the signed-in user, whatever an old client still sends
        for sent in (REPORTER_UUID, "28"):
            with patched(idrs=[MOCK_IDR_ROW], projects=ASSIGNED) as mocks:
                response = admin_client.post(self.url, json={**CREATE_BODY, "reporter_uuid": sent})
            assert response.status_code == 201
            assert mocks["idrs"].call_args.args[1][1] == ADMIN_USER_ROW["uuid"]
            assert mocks["projects"].call_args.args[1] == (ADMIN_USER_ROW["uuid"], "HWS0023")

    def test_insert_failure_returns_500(self, admin_client):
        with patched(idrs=None, projects=ASSIGNED):
            response = admin_client.post(self.url, json=CREATE_BODY)
        assert response.status_code == 500

    def test_collision_without_existing_row_returns_500(self, admin_client):
        with patched(idrs=([], []), projects=ASSIGNED):
            response = admin_client.post(self.url, json=CREATE_BODY)
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
    "is_auto_generated": False,
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

    def test_returns_200_with_idr_fields(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]):
            response = admin_client.get(self.url)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        idr = data["data"]
        assert idr["idr_id"] == IDR_ID
        assert idr["project_id"] == "HWS0023"
        assert idr["reporter_uuid"] == REPORTER_UUID
        assert idr["report_date"] == "2026-09-25"
        assert idr["status"] == "draft"

    def test_reports_nested_with_report_data_as_stored(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]):
            reports = admin_client.get(self.url).json()["data"]["reports"]
        assert len(reports) == 1
        report = reports[0]
        assert report["report_id"] == GEN_REPORT_ID
        assert report["idr_id"] == IDR_ID
        assert report["report_type"] == "GEN"
        assert report["is_addendum"] is False
        assert report["parent_report_id"] is None
        assert report["report_data"] == GEN_REPORT_DATA

    def test_page_number_present_when_null(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]):
            report = admin_client.get(self.url).json()["data"]["reports"][0]
        assert "page_number" in report
        assert report["page_number"] is None

    def test_no_reports_returns_empty_list(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            idr = admin_client.get(self.url).json()["data"]
        assert idr["reports"] == []

    def test_addendum_with_arbitrary_report_data(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW, MOCK_ADDENDUM_ROW]):
            reports = admin_client.get(self.url).json()["data"]["reports"]
        addendum = reports[1]
        assert addendum["is_addendum"] is True
        assert addendum["parent_report_id"] == GEN_REPORT_ID
        assert addendum["report_data"] == MOCK_ADDENDUM_ROW["report_data"]

    def test_submitted_idr_includes_submit_fields_and_page_numbers(self, admin_client):
        pages = [
            {**MOCK_GEN_REPORT_ROW, "page_number": 1},
            {**MOCK_ADDENDUM_ROW, "page_number": 2},
        ]
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW], idr_reports=pages):
            idr = admin_client.get(self.url).json()["data"]
        assert idr["status"] == "submitted"
        assert idr["submitted_at"] == "2026-09-25T15:30:00Z"
        assert idr["total_pages"] == 2
        assert [r["page_number"] for r in idr["reports"]] == [1, 2]

    def test_queries_read_both_tables_by_idr_id(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            admin_client.get(self.url)
        idr_sql, idr_params = mocks["idrs"].call_args.args
        assert "FROM icid.idrs" in idr_sql
        assert idr_params == (UUID(IDR_ID),)
        reports_sql, reports_params = mocks["idr_reports"].call_args.args
        assert "FROM icid.idr_reports" in reports_sql
        assert reports_params == (UUID(IDR_ID),)

    def test_reports_ordered_by_page_then_creation(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            admin_client.get(self.url)
        sql = mocks["idr_reports"].call_args.args[0]
        assert "ORDER BY page_number NULLS LAST, created_at" in sql

    def test_missing_idr_returns_404(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.get(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_non_uuid_idr_id_returns_422(self, admin_client):
        response = admin_client.get("/v1/idrs/IDR1")
        assert response.status_code == 422

    def test_reports_query_failure_returns_500(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=None):
            response = admin_client.get(self.url)
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

    def test_returns_201_with_new_report(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]):
            response = admin_client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        report = data["data"]
        assert report["report_id"] == NEW_REPORT_ID
        assert report["idr_id"] == IDR_ID
        assert report["report_type"] == "SWR"
        assert report["report_data"] == {}
        assert report["page_number"] is None

    def test_swcb_report_can_be_created(self, admin_client):
        swcb_row = {**MOCK_NEW_SWR_ROW, "report_type": "SWCB"}
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[swcb_row]) as mocks:
            response = admin_client.post(self.url, json={"report_type": "SWCB"})
        assert response.status_code == 201
        assert response.json()["data"]["report_type"] == "SWCB"
        assert mocks["idr_reports"].call_args.args[1] == (UUID(IDR_ID), "SWCB", False, None)

    def test_insert_defaults_to_main_report_without_parent(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            admin_client.post(self.url, json={"report_type": "SWR"})
        assert mocks["idr_reports"].call_count == 1
        sql, params = mocks["idr_reports"].call_args.args
        assert "INSERT INTO icid.idr_reports" in sql
        assert params == (UUID(IDR_ID), "SWR", False, None)

    def test_insert_guards_second_general_with_on_conflict(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            admin_client.post(self.url, json={"report_type": "SWR"})
        sql = mocks["idr_reports"].call_args.args[0]
        assert "ON CONFLICT (idr_id) WHERE report_type = 'GEN' AND is_addendum = false DO NOTHING" in sql

    def test_addendum_types_default_to_not_addendum(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            admin_client.post(self.url, json={"report_type": "SKETCH"})
        assert mocks["idr_reports"].call_args.args[1][2] is False

    def test_touches_idr_updated_at(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            admin_client.post(self.url, json={"report_type": "SWR"})
        assert mocks["idrs"].call_count == 2
        sql, params = mocks["idrs"].call_args.args
        assert "UPDATE icid.idrs" in sql
        assert "updated_at = now()" in sql
        assert params == (UUID(IDR_ID),)

    def test_standalone_addendum_without_parent_is_allowed(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            response = admin_client.post(self.url, json={"report_type": "FIELD_MEMO", "is_addendum": True})
        assert response.status_code == 201
        assert mocks["idr_reports"].call_args.args[1] == (UUID(IDR_ID), "FIELD_MEMO", True, None)

    def test_addendum_with_parent_in_same_idr(self, admin_client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": GEN_REPORT_ID}
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=([MOCK_GEN_REPORT_ROW], [MOCK_NEW_SKETCH_ROW])) as mocks:
            response = admin_client.post(self.url, json=body)
        assert response.status_code == 201
        assert response.json()["data"]["parent_report_id"] == GEN_REPORT_ID
        parent_sql, parent_params = mocks["idr_reports"].call_args_list[0].args
        assert "WHERE idr_id = %s AND report_id = %s" in parent_sql
        assert parent_params == (UUID(IDR_ID), UUID(GEN_REPORT_ID))
        assert mocks["idr_reports"].call_args.args[1] == (UUID(IDR_ID), "SKETCH", True, UUID(GEN_REPORT_ID))

    def test_second_general_returns_409_with_existing_id(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW],), idr_reports=GEN_COLLISION) as mocks:
            response = admin_client.post(self.url, json={"report_type": "GEN"})
        assert response.status_code == 409
        assert response.json() == {
            "detail": "IDR already has a General report",
            "existing_report_id": GEN_REPORT_ID,
        }
        lookup_sql, lookup_params = mocks["idr_reports"].call_args.args
        assert "report_type = 'GEN' AND is_addendum = false" in lookup_sql
        assert lookup_params == (UUID(IDR_ID),)
        assert mocks["idrs"].call_count == 1  # no touch on conflict

    def test_missing_idr_returns_404(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_submitted_idr_returns_409(self, admin_client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW]) as mocks:
            response = admin_client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be edited"
        mocks["idr_reports"].assert_not_called()

    def test_parent_on_main_report_returns_400(self, admin_client):
        body = {"report_type": "SWR", "parent_report_id": GEN_REPORT_ID}
        with patched(idrs=[MOCK_IDR_ROW]) as mocks:
            response = admin_client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "Only addendums can have a parent report"
        mocks["idr_reports"].assert_not_called()

    def test_parent_not_in_this_idr_returns_400(self, admin_client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": GEN_REPORT_ID}
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = admin_client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "Parent report not found in this IDR"
        assert mocks["idr_reports"].call_count == 1  # lookup only, no insert

    def test_addendum_parent_returns_400(self, admin_client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": ADDENDUM_REPORT_ID}
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_ADDENDUM_ROW]) as mocks:
            response = admin_client.post(self.url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == "An addendum's parent must be a main report, not another addendum"
        assert mocks["idr_reports"].call_count == 1

    def test_unknown_report_type_returns_422(self, admin_client):
        with patched() as mocks:
            response = admin_client.post(self.url, json={"report_type": "WM"})
        assert response.status_code == 422
        assert "GEN" in str(response.json()["detail"])
        mocks["idrs"].assert_not_called()

    def test_missing_report_type_returns_422(self, admin_client):
        response = admin_client.post(self.url, json={"is_addendum": True})
        assert response.status_code == 422

    def test_non_uuid_idr_id_returns_422(self, admin_client):
        response = admin_client.post("/v1/idrs/IDR1/reports", json={"report_type": "SWR"})
        assert response.status_code == 422

    def test_non_uuid_parent_returns_422(self, admin_client):
        body = {"report_type": "SKETCH", "is_addendum": True, "parent_report_id": "R1"}
        response = admin_client.post(self.url, json=body)
        assert response.status_code == 422

    def test_insert_failure_returns_500(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=None):
            response = admin_client.post(self.url, json={"report_type": "SWR"})
        assert response.status_code == 500

    def test_collision_without_existing_general_returns_500(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=([], [])):
            response = admin_client.post(self.url, json={"report_type": "GEN"})
        assert response.status_code == 500


class TestReportTypeEnum:
    def test_has_22_types(self):
        assert len(ReportType) == 22

    def test_swcb_is_a_main_report_type_with_its_label(self):
        assert ReportType("SWCB") == ReportType.SWCB
        assert ReportType.SWCB not in ADDENDUM_TYPES
        assert label_for("SWCB") == "Sidewalk, Curb, Concrete Base"

    def test_ac_is_a_main_report_type_with_its_label(self):
        assert ReportType("AC") == ReportType.AC
        assert ReportType.AC not in ADDENDUM_TYPES
        assert TYPE_LABELS["AC"] == "Asphaltic Concrete"

    def test_every_type_has_a_label(self):
        assert set(TYPE_LABELS) == {t.value for t in ReportType}

    def test_addendum_types_are_report_types_and_exclude_dsp(self):
        assert ADDENDUM_TYPES <= set(ReportType)
        assert ReportType.DSP not in ADDENDUM_TYPES
        assert len(ADDENDUM_TYPES) == 6


# ---------------------------------------------------------------------------
# PUT /v1/idrs/{idr_id}/reports/{report_id}
# ---------------------------------------------------------------------------

# Deliberately odd shape: camelCase, deep nesting, keys no model knows about.
SAVE_BODY = {
    "description": "Poured sidewalk flag at 12 Main St.",
    "payItems": [{"itemNo": "4.02", "payQuantity": "12"}],
    "nested": {"deeper": {"deepest": [1, "two", None, True]}},
    "someFutureField": "frontend owns this",
}

MOCK_SAVED_ROW = {**MOCK_GEN_REPORT_ROW, "report_data": SAVE_BODY}


class TestSaveReportData:
    url = f"/v1/idrs/{IDR_ID}/reports/{GEN_REPORT_ID}"

    def test_returns_200_with_saved_report(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_SAVED_ROW]):
            response = admin_client.put(self.url, json=SAVE_BODY)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["report_id"] == GEN_REPORT_ID
        assert data["data"]["idr_id"] == IDR_ID
        assert data["data"]["report_data"] == SAVE_BODY

    def test_stores_body_as_jsonb_unchanged(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_SAVED_ROW]) as mocks:
            admin_client.put(self.url, json=SAVE_BODY)
        params = mocks["idr_reports"].call_args.args[1]
        assert isinstance(params[0], Jsonb)
        assert params[0].obj == SAVE_BODY
        assert params[1:] == (UUID(IDR_ID), UUID(GEN_REPORT_ID))

    def test_single_statement_replaces_data_and_stamps_both_updated_at(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_SAVED_ROW]) as mocks:
            admin_client.put(self.url, json=SAVE_BODY)
        assert mocks["idr_reports"].call_count == 1
        assert mocks["idrs"].call_count == 1  # IDR lookup only; the touch is inside the CTE
        sql = mocks["idr_reports"].call_args.args[0]
        assert "UPDATE icid.idr_reports" in sql
        assert "SET report_data = %s, updated_at = now()" in sql
        assert "UPDATE icid.idrs" in sql
        assert sql.count("updated_at = now()") == 2
        assert "||" not in sql  # replace, not merge

    def test_update_scoped_to_report_in_draft_idr(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_SAVED_ROW]) as mocks:
            admin_client.put(self.url, json=SAVE_BODY)
        sql = mocks["idr_reports"].call_args.args[0]
        assert "r.idr_id = %s AND r.report_id = %s" in sql
        assert "i.status = 'draft'" in sql

    def test_swcb_report_stores_arbitrary_body_unchanged(self, admin_client):
        swcb_body = {
            "description": "Replaced 3 sidewalk flags.",
            "sidewalk": {"flags": [{"sqft": "25", "thickness": "4in"}]},
            "curb": None,
        }
        swcb_row = {**MOCK_GEN_REPORT_ROW, "report_type": "SWCB", "report_data": swcb_body}
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[swcb_row]) as mocks:
            response = admin_client.put(self.url, json=swcb_body)
        assert response.status_code == 200
        assert response.json()["data"]["report_type"] == "SWCB"
        assert response.json()["data"]["report_data"] == swcb_body
        assert mocks["idr_reports"].call_args.args[1][0].obj == swcb_body

    def test_empty_object_is_accepted(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            response = admin_client.put(self.url, json={})
        assert response.status_code == 200
        assert mocks["idr_reports"].call_args.args[1][0].obj == {}

    def test_addendum_report_can_be_saved(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{ADDENDUM_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_ADDENDUM_ROW]):
            response = admin_client.put(url, json=MOCK_ADDENDUM_ROW["report_data"])
        assert response.status_code == 200
        assert response.json()["data"]["is_addendum"] is True

    def test_missing_idr_returns_404(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.put(self.url, json=SAVE_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_submitted_idr_returns_409(self, admin_client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW]) as mocks:
            response = admin_client.put(self.url, json=SAVE_BODY)
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be edited"
        mocks["idr_reports"].assert_not_called()

    def test_report_not_in_idr_returns_404(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            response = admin_client.put(self.url, json=SAVE_BODY)
        assert response.status_code == 404
        assert response.json()["detail"] == "Report not found in this IDR"

    def test_array_body_returns_422_with_message(self, admin_client):
        with patched() as mocks:
            response = admin_client.put(self.url, json=[{"a": 1}])
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == "report_data must be a JSON object"
        mocks["idrs"].assert_not_called()

    def test_scalar_bodies_return_422(self, admin_client):
        for body in ["text", 42, True, None]:
            response = admin_client.put(self.url, json=body)
            assert response.status_code == 422, body

    def test_missing_body_returns_422(self, admin_client):
        response = admin_client.put(self.url)
        assert response.status_code == 422

    def test_non_uuid_ids_return_422(self, admin_client):
        assert admin_client.put(f"/v1/idrs/IDR1/reports/{GEN_REPORT_ID}", json={}).status_code == 422
        assert admin_client.put(f"/v1/idrs/{IDR_ID}/reports/R1", json={}).status_code == 422

    def test_update_failure_returns_500(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=None):
            response = admin_client.put(self.url, json=SAVE_BODY)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# PUT /v1/idrs/{idr_id}/header
# ---------------------------------------------------------------------------

FULL_HEADER = {
    "work_start_time": "07:00",
    "work_end_time": "15:30",
    "inspector_start_time": "06:45",
    "inspector_end_time": "16:00",
    "temp_low": 58,
    "temp_high": 74.5,
    "weather_am": "Clear",
    "weather_pm": "Cloudy",
}

MOCK_HEADER_ROW = {
    **MOCK_IDR_ROW,
    "work_start_time": time(7, 0),
    "work_end_time": time(15, 30),
    "inspector_start_time": time(6, 45),
    "inspector_end_time": time(16, 0),
    "temp_low": Decimal("58.0"),
    "temp_high": Decimal("74.5"),
    "weather_am": "Clear",
    "weather_pm": "Cloudy",
}


def header_calls(row=MOCK_HEADER_ROW, stored=MOCK_IDR_ROW):
    """
    Side effects for api.queries.idrs.run_query on a header save: the IDR read, then the UPDATE.
    Takes the row the UPDATE returns and the row the read returns.
    Returns the side_effect tuple.
    """
    return ([stored], [row])


class TestSaveHeader:
    url = f"/v1/idrs/{IDR_ID}/header"

    def test_returns_200_with_full_idr(self, admin_client):
        with patched(idrs=header_calls()):
            response = admin_client.put(self.url, json=FULL_HEADER)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        idr = data["data"]
        assert idr["idr_id"] == IDR_ID
        assert idr["work_start_time"] == "07:00:00"
        assert idr["temp_low"] == 58.0
        assert idr["temp_high"] == 74.5
        assert idr["weather_pm"] == "Cloudy"
        assert "reports" not in idr

    def test_all_fields_reach_the_update(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            admin_client.put(self.url, json=FULL_HEADER)
        sql, params = mocks["idrs"].call_args.args
        for column in FULL_HEADER:
            assert f"{column} = %s" in sql
        assert params == (time(7, 0), time(15, 30), time(6, 45), time(16, 0), 58.0, 74.5, "Clear", "Cloudy", UUID(IDR_ID))

    def test_omitted_fields_are_not_touched(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            admin_client.put(self.url, json={"weather_am": "Rain"})
        sql, params = mocks["idrs"].call_args.args
        assert "weather_am = %s" in sql
        for column in FULL_HEADER:
            if column != "weather_am":
                assert column not in sql.split("WHERE")[0].split("SET")[1]
        assert params == ("Rain", UUID(IDR_ID))

    def test_explicit_null_clears_the_field(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            response = admin_client.put(self.url, json={"weather_am": None})
        assert response.status_code == 200
        sql, params = mocks["idrs"].call_args.args
        assert "weather_am = %s" in sql
        assert params == (None, UUID(IDR_ID))

    def test_update_is_atomic_draft_guard_and_stamps_updated_at(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            admin_client.put(self.url, json={"weather_am": "Rain"})
        assert mocks["idrs"].call_count == 2
        sql = mocks["idrs"].call_args.args[0]
        assert "UPDATE icid.idrs" in sql
        assert "updated_at = now()" in sql
        assert "WHERE idr_id = %s AND status = 'draft'" in sql

    def test_empty_body_only_stamps_updated_at(self, admin_client):
        with patched(idrs=header_calls(row=MOCK_IDR_ROW)) as mocks:
            response = admin_client.put(self.url, json={})
        assert response.status_code == 200
        sql, params = mocks["idrs"].call_args.args
        assert "SET updated_at = now()" in sql
        assert params == (UUID(IDR_ID),)

    def test_seconds_in_time_are_accepted(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            response = admin_client.put(self.url, json={"work_start_time": "07:00:30"})
        assert response.status_code == 200
        assert mocks["idrs"].call_args.args[1][0] == time(7, 0, 30)

    def test_overnight_work_times_are_allowed(self, admin_client):
        with patched(idrs=header_calls()):
            response = admin_client.put(self.url, json={"work_start_time": "22:00", "work_end_time": "06:00"})
        assert response.status_code == 200

    def test_temp_low_above_temp_high_returns_400(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW],)) as mocks:
            response = admin_client.put(self.url, json={"temp_low": 80, "temp_high": 60})
        assert response.status_code == 400
        assert response.json()["detail"] == "temp_low cannot be greater than temp_high"
        assert mocks["idrs"].call_count == 1  # no update

    def test_temp_order_checked_against_stored_value(self, admin_client):
        stored = {**MOCK_IDR_ROW, "temp_low": Decimal("70.0")}
        with patched(idrs=([stored],)):
            response = admin_client.put(self.url, json={"temp_high": 65})
        assert response.status_code == 400

    def test_temp_order_skipped_when_other_side_cleared(self, admin_client):
        stored = {**MOCK_IDR_ROW, "temp_low": Decimal("70.0")}
        with patched(idrs=header_calls(stored=stored)):
            response = admin_client.put(self.url, json={"temp_low": None, "temp_high": 65})
        assert response.status_code == 200

    def test_missing_idr_returns_404(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.put(self.url, json={"weather_am": "Rain"})
        assert response.status_code == 404
        assert mocks["idrs"].call_count == 1

    def test_submitted_idr_returns_409(self, admin_client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW]) as mocks:
            response = admin_client.put(self.url, json={"weather_am": "Rain"})
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be edited"
        assert mocks["idrs"].call_count == 1

    def test_submitted_between_check_and_update_returns_409(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW], [])):
            response = admin_client.put(self.url, json={"weather_am": "Rain"})
        assert response.status_code == 409

    def test_disallowed_field_returns_422_naming_it(self, admin_client):
        with patched() as mocks:
            response = admin_client.put(self.url, json={"weather_am": "Rain", "status": "submitted"})
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == "Field 'status' cannot be edited via this endpoint."
        mocks["idrs"].assert_not_called()

    def test_several_disallowed_fields_are_all_named(self, admin_client):
        response = admin_client.put(self.url, json={"weatherAM": "Clear", "report_date": "2026-09-01"})
        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"] == (
            "Fields 'report_date', 'weatherAM' cannot be edited via this endpoint."
        )

    def test_malformed_time_returns_422(self, admin_client):
        response = admin_client.put(self.url, json={"work_start_time": "7am"})
        assert response.status_code == 422

    def test_extreme_temps_are_accepted(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            response = admin_client.put(self.url, json={"temp_low": -60, "temp_high": 130})
        assert response.status_code == 200
        assert mocks["idrs"].call_args.args[1] == (-60.0, 130.0, UUID(IDR_ID))

    def test_fractional_seconds_in_time_are_accepted(self, admin_client):
        with patched(idrs=header_calls()) as mocks:
            response = admin_client.put(self.url, json={"work_start_time": "07:00:30.123456"})
        assert response.status_code == 200
        assert mocks["idrs"].call_args.args[1][0] == time(7, 0, 30, 123456)

    def test_non_uuid_idr_id_returns_422(self, admin_client):
        response = admin_client.put("/v1/idrs/IDR1/header", json={"weather_am": "Rain"})
        assert response.status_code == 422

    def test_update_failure_returns_500(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW], None)):
            response = admin_client.put(self.url, json={"weather_am": "Rain"})
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /v1/idrs/{idr_id}/reports/{report_id}
# ---------------------------------------------------------------------------

# delete_idr_report now returns the deleted row's classification fields so the
# endpoint can decide whether to regenerate / dismiss the auto-General.
DELETED_SWR = [{
    "report_id": UUID(NEW_REPORT_ID),
    "report_type": "SWR",
    "is_addendum": False,
    "is_auto_generated": False,
}]
DELETED_INSPECTOR_GEN = [{
    "report_id": UUID(GEN_REPORT_ID),
    "report_type": "GEN",
    "is_addendum": False,
    "is_auto_generated": False,
}]
DELETED_ADDENDUM = [{
    "report_id": UUID(ADDENDUM_REPORT_ID),
    "report_type": "SKETCH",
    "is_addendum": True,
    "is_auto_generated": False,
}]


class TestDeleteReport:
    url = f"/v1/idrs/{IDR_ID}/reports/{NEW_REPORT_ID}"

    def test_returns_204_with_empty_body(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_SWR):
            response = admin_client.delete(self.url)
        assert response.status_code == 204
        assert response.content == b""

    def test_single_statement_deletes_scoped_to_draft_and_stamps_idr(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_SWR) as mocks:
            admin_client.delete(self.url)
        assert mocks["idrs"].call_count == 1  # IDR lookup only; the touch is inside the CTE
        assert mocks["idr_reports"].call_count == 2  # report lookup (before Storage), then the delete
        sql, params = mocks["idr_reports"].call_args.args
        assert "DELETE FROM icid.idr_reports" in sql
        assert "r.idr_id = %s AND r.report_id = %s" in sql
        assert "i.status = 'draft'" in sql
        assert "UPDATE icid.idrs" in sql
        assert "updated_at = now()" in sql
        assert params == (UUID(IDR_ID), UUID(NEW_REPORT_ID))

    def test_general_can_be_deleted(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{GEN_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_INSPECTOR_GEN):
            response = admin_client.delete(url)
        assert response.status_code == 204

    def test_addendum_can_be_deleted(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{ADDENDUM_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_ADDENDUM) as mocks:
            response = admin_client.delete(url)
        assert response.status_code == 204
        assert mocks["idr_reports"].call_args.args[1] == (UUID(IDR_ID), UUID(ADDENDUM_REPORT_ID))

    def test_addendums_left_to_cascade_not_deleted_by_code(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{GEN_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_INSPECTOR_GEN) as mocks:
            admin_client.delete(url)
        assert mocks["idr_reports"].call_count == 2  # report lookup, then the delete
        assert "parent_report_id" not in mocks["idr_reports"].call_args.args[0]

    def test_missing_idr_returns_404(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.delete(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_submitted_idr_returns_409(self, admin_client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW]) as mocks:
            response = admin_client.delete(self.url)
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be edited"
        mocks["idr_reports"].assert_not_called()

    def test_report_not_in_idr_returns_404(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            response = admin_client.delete(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "Report not found in this IDR"

    def test_non_uuid_ids_return_422(self, admin_client):
        assert admin_client.delete(f"/v1/idrs/IDR1/reports/{NEW_REPORT_ID}").status_code == 422
        assert admin_client.delete(f"/v1/idrs/{IDR_ID}/reports/R1").status_code == 422

    def test_delete_failure_returns_500(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=(DELETED_SWR, None)):
            response = admin_client.delete(self.url)
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /v1/idrs/{idr_id}/reports/{report_id} — attachment cleanup
# Runs the real Storage hook; only run_query and the Supabase client are mocked.
# ---------------------------------------------------------------------------

REPORT_FILE = f"{NEW_REPORT_ID}/3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f_site.jpg"
ADDENDUM_FILE = f"{ADDENDUM_REPORT_ID}/4d5e6f7a-8b9c-4d0e-9f1a-2b3c4d5e6f70_sketch.pdf"
STORAGE_PATH_ROWS = [{"storage_path": REPORT_FILE}, {"storage_path": ADDENDUM_FILE}]


@contextmanager
def patched_report_delete(storage_paths, idr_reports=DELETED_SWR):
    """
    Patch everything a report delete touches except the attachment cleanup hook itself.
    Takes the rows the storage-path lookup returns and the idr_reports run_query result.
    Yields a dict with the idr_reports and report_attachments run_query mocks and the mock Storage bucket.
    """
    client = MagicMock()
    with patch("api.queries.idrs.run_query", return_value=[MOCK_IDR_ROW]), \
         patch("api.queries.idr_reports.run_query", return_value=idr_reports) as ir, \
         patch("api.queries.report_attachments.run_query", return_value=storage_paths) as a, \
         patch("api.storage.client.get_client", return_value=client), \
         patch("api.v1.idrs.regenerate_auto_general"), \
         patch("api.v1.idrs.set_dismissed_auto_general"):
        yield {"idr_reports": ir, "attachments": a, "bucket": client.storage.from_.return_value}


class TestDeleteReportAttachments:
    url = f"/v1/idrs/{IDR_ID}/reports/{NEW_REPORT_ID}"

    def test_removes_report_and_addendum_files_before_deleting_the_row(self, admin_client):
        # One parent mock records Storage removes and idr_reports queries in the order they happen.
        timeline = MagicMock()
        with patched_report_delete(STORAGE_PATH_ROWS) as mocks:
            timeline.attach_mock(mocks["bucket"].remove, "storage_remove")
            timeline.attach_mock(mocks["idr_reports"], "idr_reports_query")
            response = admin_client.delete(self.url)

        assert response.status_code == 204
        ids = (UUID(IDR_ID), UUID(NEW_REPORT_ID))
        assert timeline.mock_calls == [
            call.idr_reports_query(ANY, ids),        # report lookup (scopes the files to this IDR)
            call.storage_remove([REPORT_FILE]),      # the report's own file
            call.storage_remove([ADDENDUM_FILE]),    # its addendum's file
            call.idr_reports_query(ANY, ids),        # the report delete, last
        ]
        lookup_sql = timeline.mock_calls[0].args[0]
        delete_sql = timeline.mock_calls[-1].args[0]
        assert "DELETE" not in lookup_sql
        assert "DELETE FROM icid.idr_reports" in delete_sql

    def test_partial_storage_failure_keeps_going_and_still_deletes(self, admin_client, caplog):
        files = [f"{NEW_REPORT_ID}/{n}_photo{n}.jpg" for n in (1, 2, 3)]
        with caplog.at_level(logging.WARNING, logger="api.services.attachments"):
            with patched_report_delete([{"storage_path": f} for f in files]) as mocks:
                # First remove succeeds, second raises, third succeeds.
                mocks["bucket"].remove.side_effect = [None, RuntimeError("storage timeout"), None]
                response = admin_client.delete(self.url)

        assert response.status_code == 204
        # All three were attempted, in order: the failure on the second didn't stop the third.
        assert mocks["bucket"].remove.call_args_list == [call([files[0]]), call([files[1]]), call([files[2]])]
        # Exactly one warning, naming only the file that failed.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert files[1] in warnings[0].getMessage()
        assert files[0] not in caplog.text and files[2] not in caplog.text
        # Best-effort: the report row was still deleted, after the Storage attempts.
        assert "DELETE FROM icid.idr_reports" in mocks["idr_reports"].call_args.args[0]

    def test_file_lookup_walks_the_addendum_tree(self, admin_client):
        with patched_report_delete(STORAGE_PATH_ROWS) as mocks:
            admin_client.delete(self.url)
        sql, params = mocks["attachments"].call_args.args
        assert "WITH RECURSIVE" in sql
        assert "r.parent_report_id = t.report_id" in sql
        assert "icid.report_attachments" in sql
        assert params == (UUID(NEW_REPORT_ID),)

    def test_pending_and_uploaded_files_are_both_removed(self, admin_client):
        # REPORT_FILE stands for an uploaded attachment, PENDING_FILE for one whose client
        # uploaded the file but never called upload-complete. Both must leave Storage.
        pending_file = f"{NEW_REPORT_ID}/5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7081_pending.jpg"
        with patched_report_delete([{"storage_path": REPORT_FILE}, {"storage_path": pending_file}]) as mocks:
            response = admin_client.delete(self.url)
        assert response.status_code == 204
        assert "is_uploaded" not in mocks["attachments"].call_args.args[0]  # the lookup doesn't filter pending rows
        assert mocks["bucket"].remove.call_args_list == [call([REPORT_FILE]), call([pending_file])]

    def test_attachment_rows_are_left_to_the_cascade(self, admin_client):
        with patched_report_delete(STORAGE_PATH_ROWS) as mocks:
            admin_client.delete(self.url)
        assert mocks["attachments"].call_count == 1  # the path lookup only; no DELETE of attachment rows
        schema = Path(__file__).resolve().parents[2].joinpath("schema.sql").read_text()
        table = schema.split("CREATE TABLE icid.report_attachments", 1)[1].split(");", 1)[0]
        assert "REFERENCES icid.idr_reports(report_id) ON DELETE CASCADE" in table

    def test_report_without_attachments_skips_storage(self, admin_client):
        with patched_report_delete([]) as mocks:
            response = admin_client.delete(self.url)
        assert response.status_code == 204
        mocks["bucket"].remove.assert_not_called()

    def test_storage_failure_still_deletes_the_report(self, admin_client, caplog):
        with caplog.at_level(logging.WARNING, logger="api.services.attachments"):
            with patched_report_delete(STORAGE_PATH_ROWS) as mocks:
                mocks["bucket"].remove.side_effect = RuntimeError("storage down")
                response = admin_client.delete(self.url)
        assert response.status_code == 204
        assert "DELETE FROM icid.idr_reports" in mocks["idr_reports"].call_args.args[0]
        assert REPORT_FILE in caplog.text and ADDENDUM_FILE in caplog.text

    def test_file_lookup_failure_still_deletes_the_report(self, admin_client, caplog):
        with caplog.at_level(logging.WARNING, logger="api.services.attachments"):
            with patched_report_delete(None) as mocks:
                response = admin_client.delete(self.url)
        assert response.status_code == 204
        mocks["bucket"].remove.assert_not_called()
        assert "DELETE FROM icid.idr_reports" in mocks["idr_reports"].call_args.args[0]
        assert "left orphaned" in caplog.text

    def test_report_not_in_idr_touches_no_files(self, admin_client):
        with patched_report_delete(STORAGE_PATH_ROWS, idr_reports=[]) as mocks:
            response = admin_client.delete(self.url)
        assert response.status_code == 404
        mocks["attachments"].assert_not_called()
        mocks["bucket"].remove.assert_not_called()


# ---------------------------------------------------------------------------
# GET /v1/idrs/?project_id=&status=&reporter_uuid=
# ---------------------------------------------------------------------------

MOCK_LIST_DRAFT_ROW = {**MOCK_IDR_ROW, "report_count": 3, "has_general": True}
MOCK_LIST_EMPTY_DRAFT_ROW = {
    **MOCK_IDR_ROW,
    "idr_id": UUID(EXISTING_IDR_ID),
    "report_count": 0,
    "has_general": False,
}
MOCK_LIST_SUBMITTED_ROW = {**MOCK_SUBMITTED_IDR_ROW, "report_count": 2, "has_general": True}


class TestListIdrs:
    url = "/v1/idrs/"

    def test_returns_200_with_summary_fields(self, admin_client):
        with patched(idrs=[MOCK_LIST_DRAFT_ROW, MOCK_LIST_EMPTY_DRAFT_ROW]):
            response = admin_client.get(self.url, params={"project_id": "HWS0023"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert len(data["data"]) == 2
        first, second = data["data"]
        assert first["idr_id"] == IDR_ID
        assert first["report_date"] == "2026-09-25"
        assert first["status"] == "draft"
        assert first["report_count"] == 3
        assert first["has_general"] is True
        assert second["report_count"] == 0
        assert second["has_general"] is False
        assert "reports" not in first

    def test_submitted_item_carries_submit_fields(self, admin_client):
        with patched(idrs=[MOCK_LIST_SUBMITTED_ROW]):
            item = admin_client.get(self.url, params={"status": "submitted"}).json()["data"][0]
        assert item["status"] == "submitted"
        assert item["submitted_at"] == "2026-09-25T15:30:00Z"
        assert item["total_pages"] == 2

    def test_no_matches_returns_empty_list(self, admin_client):
        with patched(idrs=[]):
            response = admin_client.get(self.url, params={"project_id": "NOPE999"})
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_all_filters_reach_the_query(self, admin_client):
        params = {"project_id": "HWS0023", "status": "draft", "reporter_uuid": REPORTER_UUID}
        with patched(idrs=[]) as mocks:
            admin_client.get(self.url, params=params)
        sql, query_params = mocks["idrs"].call_args.args
        assert "i.project_id = %s" in sql
        assert "i.status = %s" in sql
        assert "i.reporter_uuid = %s" in sql
        assert query_params == ("HWS0023", "draft", UUID(REPORTER_UUID))

    def test_each_filter_is_optional(self, admin_client):
        with patched(idrs=[]) as mocks:
            admin_client.get(self.url, params={"status": "submitted"})
        sql, query_params = mocks["idrs"].call_args.args
        assert "i.project_id = %s" not in sql
        assert "i.reporter_uuid = %s" not in sql
        assert query_params == ("submitted",)

    def test_no_filters_lists_everything(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.get(self.url)
        assert response.status_code == 200
        sql, query_params = mocks["idrs"].call_args.args
        assert "WHERE" not in sql.split("FROM icid.idrs i")[1]
        assert query_params == ()

    def test_drafts_without_reporter_are_allowed(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.get(self.url, params={"project_id": "HWS0023", "status": "draft"})
        assert response.status_code == 200
        assert mocks["idrs"].call_args.args[1] == ("HWS0023", "draft")

    def test_single_sort_by_updated_at_with_tiebreakers(self, admin_client):
        for status in ("draft", "submitted"):
            with patched(idrs=[]) as mocks:
                admin_client.get(self.url, params={"status": status})
            sql = mocks["idrs"].call_args.args[0]
            assert "ORDER BY i.updated_at DESC, i.created_at DESC, i.idr_id" in sql
            assert "submitted_at DESC" not in sql

    def test_report_count_counts_all_reports(self, admin_client):
        with patched(idrs=[]) as mocks:
            admin_client.get(self.url)
        sql = mocks["idrs"].call_args.args[0]
        count_subquery = sql.split("AS report_count")[0]
        assert "COUNT(*)" in count_subquery
        assert "is_addendum" not in count_subquery

    def test_has_general_checks_non_addendum_gen(self, admin_client):
        with patched(idrs=[]) as mocks:
            admin_client.get(self.url)
        sql = mocks["idrs"].call_args.args[0]
        assert "EXISTS" in sql
        assert "r.report_type = 'GEN' AND r.is_addendum = false" in sql

    def test_one_query_only(self, admin_client):
        with patched(idrs=[], projects=[]) as mocks:
            admin_client.get(self.url, params={"project_id": "HWS0023"})
        assert mocks["idrs"].call_count == 1
        mocks["projects"].assert_not_called()  # unknown project is [] not 404
        mocks["idr_reports"].assert_not_called()

    def test_invalid_status_returns_422(self, admin_client):
        response = admin_client.get(self.url, params={"status": "approved"})
        assert response.status_code == 422

    def test_non_uuid_reporter_returns_422(self, admin_client):
        response = admin_client.get(self.url, params={"reporter_uuid": "28"})
        assert response.status_code == 422

    def test_query_failure_returns_500(self, admin_client):
        with patched(idrs=None):
            response = admin_client.get(self.url, params={"project_id": "HWS0023"})
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/submit
# ---------------------------------------------------------------------------

MOCK_JUST_SUBMITTED_ROW = {**MOCK_IDR_ROW, "status": "submitted", "submitted_at": NOW, "total_pages": 2}

NUMBERED_REPORTS = [
    {**MOCK_GEN_REPORT_ROW, "page_number": 1},
    {**MOCK_ADDENDUM_ROW, "page_number": 2},
]

# api.queries.idrs: the IDR read, then the submit statement.
DRAFT_THEN_SUBMITTED_IDR = ([MOCK_IDR_ROW], [MOCK_JUST_SUBMITTED_ROW])

# api.queries.idr_reports: the pre-submit report list, then the numbered list.
REPORTS_THEN_NUMBERED = (
    [MOCK_GEN_REPORT_ROW, MOCK_ADDENDUM_ROW],
    NUMBERED_REPORTS,
)


class TestSubmitIdr:
    url = f"/v1/idrs/{IDR_ID}/submit"

    def submit_sql(self, admin_client):
        """
        Submit through the endpoint with a happy-path setup and capture the submit statement.
        Takes the test client.
        Returns the (sql, params) the submit statement was run with.
        """
        with patched(idrs=DRAFT_THEN_SUBMITTED_IDR, idr_reports=REPORTS_THEN_NUMBERED) as mocks:
            admin_client.post(self.url)
        return mocks["idrs"].call_args.args

    def test_returns_200_with_submitted_idr_and_numbered_reports(self, admin_client):
        with patched(idrs=DRAFT_THEN_SUBMITTED_IDR, idr_reports=REPORTS_THEN_NUMBERED):
            response = admin_client.post(self.url)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        idr = data["data"]
        assert idr["idr_id"] == IDR_ID
        assert idr["status"] == "submitted"
        assert idr["submitted_at"] == "2026-09-25T15:30:00Z"
        assert idr["total_pages"] == 2
        assert [r["page_number"] for r in idr["reports"]] == [1, 2]
        assert idr["reports"][1]["parent_report_id"] == GEN_REPORT_ID

    def test_submit_statement_takes_the_idr_id_and_the_signature_copy(self, admin_client):
        _, params = self.submit_sql(admin_client)
        assert params == (UUID(IDR_ID), SIGNATURE_COPY)

    def test_locks_draft_row_before_numbering(self, admin_client):
        sql, _ = self.submit_sql(admin_client)
        assert "WHERE idr_id = %s AND status = 'draft'" in sql
        assert "FOR UPDATE" in sql

    def test_sets_status_timestamps_and_total_pages(self, admin_client):
        sql, _ = self.submit_sql(admin_client)
        assert "status = 'submitted'" in sql
        assert "submitted_at = now()" in sql
        assert "total_pages = (SELECT COUNT(*) FROM ordered)" in sql
        assert sql.count("updated_at = now()") == 2  # the IDR and every numbered report

    def test_numbers_every_report_in_page_order(self, admin_client):
        sql, _ = self.submit_sql(admin_client)
        assert "ROW_NUMBER() OVER" in sql
        assert "SET page_number = o.page_number" in sql
        order = sql.split("ORDER BY")[1].split(") AS page_number")[0]
        keys = [line.strip().rstrip(",") for line in order.strip().splitlines()]
        assert keys == [
            "(r.is_addendum AND r.parent_report_id IS NULL)",
            "(COALESCE(p.report_type, r.report_type) = 'GEN') DESC",
            "COALESCE(p.created_at, r.created_at)",
            "COALESCE(p.report_id, r.report_id)",
            "r.is_addendum",
            "r.created_at",
            "r.report_id",
        ]

    def test_statement_refuses_empty_idr(self, admin_client):
        sql, _ = self.submit_sql(admin_client)
        assert "EXISTS (SELECT 1 FROM ordered)" in sql

    def test_addendum_only_idr_can_be_submitted(self, admin_client):
        standalone = {**MOCK_ADDENDUM_ROW, "parent_report_id": None}
        with patched(idrs=DRAFT_THEN_SUBMITTED_IDR, idr_reports=([standalone], [{**standalone, "page_number": 1}])):
            response = admin_client.post(self.url)
        assert response.status_code == 200

    def test_missing_idr_returns_404(self, admin_client):
        with patched(idrs=[]) as mocks:
            response = admin_client.post(self.url)
        assert response.status_code == 404
        assert response.json()["detail"] == "IDR not found"
        mocks["idr_reports"].assert_not_called()

    def test_already_submitted_returns_409(self, admin_client):
        with patched(idrs=[MOCK_SUBMITTED_IDR_ROW]) as mocks:
            response = admin_client.post(self.url)
        assert response.status_code == 409
        assert response.json()["detail"] == "Only draft IDRs can be submitted"
        assert mocks["idrs"].call_count == 1  # no submit statement
        mocks["idr_reports"].assert_not_called()

    def test_empty_idr_returns_400(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]) as mocks:
            response = admin_client.post(self.url)
        assert response.status_code == 400
        assert response.json()["detail"] == "IDR must contain at least one report before submission."
        assert mocks["idrs"].call_count == 1  # no submit statement

    def test_concurrent_change_returns_409(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW], []), idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            response = admin_client.post(self.url)
        assert response.status_code == 409
        assert mocks["idr_reports"].call_count == 1  # no numbered re-read

    def test_non_uuid_idr_id_returns_422(self, admin_client):
        response = admin_client.post("/v1/idrs/IDR1/submit")
        assert response.status_code == 422

    def test_submit_failure_returns_500(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW], None), idr_reports=[MOCK_GEN_REPORT_ROW]):
            response = admin_client.post(self.url)
        assert response.status_code == 500

    def test_report_list_failure_returns_500(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=None) as mocks:
            response = admin_client.post(self.url)
        assert response.status_code == 500
        assert mocks["idrs"].call_count == 1  # no submit statement


# ---------------------------------------------------------------------------
# Slice R4a — auto-generated General
# ---------------------------------------------------------------------------


def _child(report_type, report_data=None):
    """
    Build a minimal contributing report row for aggregation tests.
    Takes a report type and an optional report_data dict.
    Returns a dict with just the keys the aggregator reads.
    """
    return {"report_type": report_type, "report_data": report_data or {}}


def _pay(item_no, budget_code, pay_quantity, description="", quantity_chk="", unit=None):
    """
    Build a single pay item as the frontend stores it, for aggregation tests.
    Takes itemNo, budgetCode, payQuantity and optional description / quantityChk / unit (omitted when None).
    Returns the pay item dict.
    """
    item = {
        "itemNo": item_no,
        "budgetCode": budget_code,
        "payQuantity": pay_quantity,
        "description": description,
        "quantityChk": quantity_chk,
    }
    if unit is not None:
        item["unit"] = unit
    return item


class TestBuildAutoGeneralData:
    """The pure aggregator: description composition and pay-item combining."""

    def test_description_uses_label_and_text_when_present(self):
        children = [_child("SWR", {"description": "Excavated trench along the east curb."})]
        assert build_auto_general_data(children)["description"] == (
            f"Sewer: Excavated trench along the east curb.\n\n{DESCRIPTION_FOOTER}"
        )

    def test_description_falls_back_to_label_work_when_missing(self):
        children = [_child("PILE", {}), _child("WM_1", {"description": "   "})]
        # Blank/whitespace description falls back; entries are blank-line separated.
        assert build_auto_general_data(children)["description"] == (
            f"Pile Driving work\n\nWater Main (Sheet 1) work\n\n{DESCRIPTION_FOOTER}"
        )

    def test_description_concatenates_in_order(self):
        children = [
            _child("SWR", {"description": "Trench."}),
            _child("CONC", {}),
        ]
        assert build_auto_general_data(children)["description"] == (
            f"Sewer: Trench.\n\nConcrete (Structures) work\n\n{DESCRIPTION_FOOTER}"
        )

    def test_description_always_ends_with_footer_after_a_blank_line(self):
        # Footer is appended whether children have real descriptions or the "[Type] work" fallback.
        for children in ([_child("SWR", {"description": "Real text."})], [_child("SWR", {})]):
            description = build_auto_general_data(children)["description"]
            assert description.endswith(f"\n\n{DESCRIPTION_FOOTER}")

    def test_pay_items_combine_on_item_and_budget(self):
        children = [
            _child("SWR", {"payItems": [_pay("4.21", "HWS-01", "100", "4\" Conc. Sidewalk", "RM")]}),
            _child("CONC", {"payItems": [_pay("4.21", "HWS-01", "50", "", "")]}),
        ]
        items = build_auto_general_data(children)["payItems"]
        assert items == [{
            "itemNo": "4.21",
            "budgetCode": "HWS-01",
            "description": "4\" Conc. Sidewalk",
            "unit": "",
            "payQuantity": "150.00",
            "quantityChk": "RM",
        }]

    def test_pay_items_kept_separate_on_different_budget_code(self):
        children = [
            _child("SWR", {"payItems": [_pay("4.21", "HWS-01", "100")]}),
            _child("CONC", {"payItems": [_pay("4.21", "HWS-02", "50")]}),
        ]
        items = build_auto_general_data(children)["payItems"]
        assert [(i["itemNo"], i["budgetCode"], i["payQuantity"]) for i in items] == [
            ("4.21", "HWS-01", "100.00"),
            ("4.21", "HWS-02", "50.00"),
        ]

    def test_pay_quantity_always_two_decimals(self):
        children = [
            _child("SWR", {"payItems": [_pay("6.01", "B", "100")]}),
            _child("CONC", {"payItems": [_pay("6.01", "B", "50.5")]}),
        ]
        assert build_auto_general_data(children)["payItems"][0]["payQuantity"] == "150.50"

    def test_unparseable_quantity_falls_back_to_first_non_empty(self):
        children = [
            _child("SWR", {"payItems": [_pay("6.01", "B", "100")]}),
            _child("CONC", {"payItems": [_pay("6.01", "B", "TBD")]}),
        ]
        # Any unparseable member skips summing; keeps the first non-empty raw value.
        assert build_auto_general_data(children)["payItems"][0]["payQuantity"] == "100"

    def test_unparseable_skips_blank_and_keeps_first_real_string(self):
        children = [
            _child("SWR", {"payItems": [_pay("6.01", "B", "")]}),
            _child("CONC", {"payItems": [_pay("6.01", "B", "TBD")]}),
        ]
        assert build_auto_general_data(children)["payItems"][0]["payQuantity"] == "TBD"

    def test_combined_description_and_chk_take_first_non_empty(self):
        children = [
            _child("SWR", {"payItems": [_pay("6.01", "B", "TBD", description="", quantity_chk="")]}),
            _child("CONC", {"payItems": [_pay("6.01", "B", "TBD", description="Real desc", quantity_chk="CM")]}),
        ]
        item = build_auto_general_data(children)["payItems"][0]
        assert item["description"] == "Real desc"
        assert item["quantityChk"] == "CM"

    def test_unit_carried_through_from_single_swcb_report(self):
        children = [
            _child("SWCB", {"payItems": [_pay("4.21", "HWS-01", "100", unit="SF")]}),
            _child("SWR", {"description": "x"}),
        ]
        assert build_auto_general_data(children)["payItems"][0]["unit"] == "SF"

    def test_unit_carried_through_from_multiple_consistent_reports(self, caplog):
        children = [
            _child("SWCB", {"payItems": [_pay("4.21", "HWS-01", "100", unit="SF")]}),
            _child("SWR", {"payItems": [_pay("4.21", "HWS-01", "50", unit="SF"), _pay("6.01", "B", "3", unit="TON")]}),
        ]
        with caplog.at_level(logging.WARNING, logger="api.services.auto_general"):
            items = build_auto_general_data(children)["payItems"]
        assert [(i["itemNo"], i["unit"], i["payQuantity"]) for i in items] == [
            ("4.21", "SF", "150.00"),
            ("6.01", "TON", "3.00"),
        ]
        assert caplog.records == []

    def test_missing_or_empty_unit_becomes_empty_string(self):
        children = [
            _child("SWCB", {"payItems": [_pay("4.21", "HWS-01", "100")]}),
            _child("SWR", {"payItems": [_pay("4.21", "HWS-01", "50", unit=""), _pay("6.01", "B", "3", unit=None)]}),
        ]
        items = build_auto_general_data(children)["payItems"]
        assert [i["unit"] for i in items] == ["", ""]

    def test_unit_taken_from_first_row_that_has_one(self):
        # An older child without a unit does not blank out a later child's unit.
        children = [
            _child("SWCB", {"payItems": [_pay("4.21", "HWS-01", "100")]}),
            _child("SWR", {"payItems": [_pay("4.21", "HWS-01", "50", unit="SF")]}),
        ]
        assert build_auto_general_data(children)["payItems"][0]["unit"] == "SF"

    def test_conflicting_units_first_wins_and_warns(self, caplog):
        children = [
            _child("SWCB", {"payItems": [_pay("4.21", "HWS-01", "100", unit="SF")]}),
            _child("SWR", {"payItems": [_pay("4.21", "HWS-01", "50", unit="SY")]}),
        ]
        with caplog.at_level(logging.WARNING, logger="api.services.auto_general"):
            item = build_auto_general_data(children)["payItems"][0]
        assert item["unit"] == "SF"
        assert item["payQuantity"] == "150.00"
        assert len(caplog.records) == 1
        assert "Conflicting units" in caplog.records[0].getMessage()

    def test_no_pay_items_yields_empty_list(self):
        assert build_auto_general_data([_child("SWR", {"description": "x"})])["payItems"] == []

    def test_report_data_holds_only_aggregated_keys(self):
        # Regeneration full-replaces report_data, so non-aggregated child keys must never leak in.
        children = [
            _child("SWR", {"description": "x", "payItems": [], "workforce": {"foreman": "2"}, "comments": "note"}),
            _child("CONC", {"safetyChecks": {"fencing": True}, "equipment": {"backhoe": {"model": "X"}}}),
        ]
        assert set(build_auto_general_data(children).keys()) == {"description", "payItems"}

    def test_ac_stays_out_of_the_merge(self):
        # AC prints on its own AC Fr / AC Bk, pay items included, so neither its description nor its items repeat here
        children = [
            _child("SWCB", {"description": "Poured curb.", "payItems": [_pay("4.13", "B", "10", unit="SF")]}),
            _child("AC", {"description": "Paved the lane.", "payItems": [_pay("6.01", "B", "3", unit="TON")]}),
        ]
        data = build_auto_general_data(children)
        assert data["description"] == f"Sidewalk, Curb, Concrete Base: Poured curb.\n\n{DESCRIPTION_FOOTER}"
        assert [item["itemNo"] for item in data["payItems"]] == ["4.13"]
        # Only AC: just the footer, and no items
        assert build_auto_general_data([_child("AC", {"payItems": [_pay("6.01", "B", "3")]})]) == {
            "description": DESCRIPTION_FOOTER, "payItems": []}


@contextmanager
def patched_service(idr=None, general=None, main_reports=None):
    """
    Patch the query functions regenerate_auto_general calls, isolating its case logic from SQL.
    Takes the idr row, the current General row (or None) and the non-General main report list.
    Yields a dict of the create/update/delete/list/get_general mocks.
    """
    with patch("api.services.auto_general.get_idr_by_id", return_value=idr) as gi, \
         patch("api.services.auto_general.get_general_report", return_value=general) as gg, \
         patch("api.services.auto_general.list_non_general_main_reports", return_value=main_reports or []) as lm, \
         patch("api.services.auto_general.create_auto_general") as cr, \
         patch("api.services.auto_general.update_auto_general") as up, \
         patch("api.services.auto_general.delete_auto_general") as dl:
        yield {"idr": gi, "get_general": gg, "list": lm, "create": cr, "update": up, "delete": dl}


IDR_ID_UUID = UUID(IDR_ID)
IDR_ACTIVE = {"has_dismissed_auto_general": False}
IDR_DISMISSED = {"has_dismissed_auto_general": True}
AUTO_GENERAL = {"is_auto_generated": True}
INSPECTOR_GENERAL = {"is_auto_generated": False}


class TestRegenerateAutoGeneral:
    """The orchestrator: the five behavioral cases A–E."""

    def test_case_a_single_report_creates_nothing(self, admin_client):
        with patched_service(idr=IDR_ACTIVE, general=None, main_reports=[_child("SWR")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_not_called()
        m["update"].assert_not_called()
        m["delete"].assert_not_called()

    def test_case_c_creates_auto_general_on_second_report(self, admin_client):
        with patched_service(idr=IDR_ACTIVE, general=None, main_reports=[_child("SWR"), _child("CONC")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_called_once()
        assert m["create"].call_args.args[0] == IDR_ID_UUID
        assert "description" in m["create"].call_args.args[1]
        m["update"].assert_not_called()
        m["delete"].assert_not_called()

    def test_ac_counts_toward_the_threshold_but_stays_out_of_the_merge(self, admin_client):
        children = [
            _child("SWCB", {"description": "Poured curb."}),
            _child("AC", {"description": "Paved the lane.", "payItems": [_pay("6.01", "B", "3", unit="TON")]}),
        ]
        with patched_service(idr=IDR_ACTIVE, general=None, main_reports=children) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_called_once()  # two contributing reports, AC one of them
        assert m["create"].call_args.args[1] == {
            "description": f"Sidewalk, Curb, Concrete Base: Poured curb.\n\n{DESCRIPTION_FOOTER}", "payItems": []}

    def test_swcb_contributes_to_auto_general_with_its_label(self, admin_client):
        children = [
            _child("SWCB", {"description": "Poured 40 ft of curb."}),
            _child("SWR", {"description": "Laid 20 ft of 12in pipe."}),
        ]
        with patched_service(idr=IDR_ACTIVE, general=None, main_reports=children) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_called_once()
        assert m["create"].call_args.args[1]["description"] == (
            "Sidewalk, Curb, Concrete Base: Poured 40 ft of curb.\n\n"
            "Sewer: Laid 20 ft of 12in pipe.\n\n"
            f"{DESCRIPTION_FOOTER}"
        )

    def test_case_c_refreshes_existing_auto_general(self, admin_client):
        with patched_service(idr=IDR_ACTIVE, general=AUTO_GENERAL, main_reports=[_child("SWR"), _child("CONC")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["update"].assert_called_once()
        assert m["update"].call_args.args[0] == IDR_ID_UUID
        m["create"].assert_not_called()
        m["delete"].assert_not_called()

    def test_case_b_leaves_inspector_general_untouched(self, admin_client):
        with patched_service(idr=IDR_ACTIVE, general=INSPECTOR_GENERAL, main_reports=[_child("SWR"), _child("CONC")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_not_called()
        m["update"].assert_not_called()
        m["delete"].assert_not_called()
        m["list"].assert_not_called()  # returns before even reading the children

    def test_case_e_deletes_auto_general_below_threshold(self, admin_client):
        with patched_service(idr=IDR_ACTIVE, general=AUTO_GENERAL, main_reports=[_child("SWR")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["delete"].assert_called_once_with(IDR_ID_UUID)
        m["create"].assert_not_called()
        m["update"].assert_not_called()

    def test_dismissed_idr_never_recreates(self, admin_client):
        with patched_service(idr=IDR_DISMISSED, general=None, main_reports=[_child("SWR"), _child("CONC")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_not_called()
        m["update"].assert_not_called()
        m["delete"].assert_not_called()

    def test_addendum_type_main_reports_do_not_count(self, admin_client):
        # A SKETCH filed as a main report is an addendum-by-nature type: excluded, so only one contributor remains.
        with patched_service(idr=IDR_ACTIVE, general=None, main_reports=[_child("SWR"), _child("SKETCH")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["create"].assert_not_called()

    def test_missing_idr_is_a_noop(self, admin_client):
        with patched_service(idr=None, general=None, main_reports=[_child("SWR"), _child("CONC")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        m["get_general"].assert_not_called()
        m["create"].assert_not_called()

    def test_regeneration_writes_exactly_the_aggregated_shape(self, admin_client):
        # Locks the full-replace invariant: the auto-General's report_data is only {description, payItems}.
        with patched_service(idr=IDR_ACTIVE, general=AUTO_GENERAL, main_reports=[_child("SWR"), _child("CONC")]) as m:
            regenerate_auto_general(IDR_ID_UUID)
        written = m["update"].call_args.args[1]
        assert set(written.keys()) == {"description", "payItems"}


MOCK_NEW_GEN_ROW = {**MOCK_GEN_REPORT_ROW, "report_id": UUID(NEW_REPORT_ID)}
DELETED_AUTO_GEN = [{
    "report_id": UUID(GEN_REPORT_ID),
    "report_type": "GEN",
    "is_addendum": False,
    "is_auto_generated": True,
}]


class TestAutoGeneralHooks:
    """Which mutations trigger regeneration / dismissal at the endpoint boundary."""

    reports_url = f"/v1/idrs/{IDR_ID}/reports"

    def test_post_non_general_regenerates(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            admin_client.post(self.reports_url, json={"report_type": "SWR"})
        mocks["regen"].assert_called_once_with(IDR_ID_UUID)
        mocks["dismiss"].assert_not_called()

    def test_post_general_does_not_regenerate(self, admin_client):
        with patched(idrs=DRAFT_IDR_THEN_TOUCH, idr_reports=[MOCK_NEW_GEN_ROW]) as mocks:
            admin_client.post(self.reports_url, json={"report_type": "GEN"})
        mocks["regen"].assert_not_called()

    def test_put_non_general_regenerates(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{NEW_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_NEW_SWR_ROW]) as mocks:
            admin_client.put(url, json={"description": "x"})
        mocks["regen"].assert_called_once_with(IDR_ID_UUID)

    def test_put_general_does_not_regenerate(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{GEN_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            admin_client.put(url, json={"description": "x"})
        mocks["regen"].assert_not_called()

    def test_delete_non_general_regenerates(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{NEW_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_SWR) as mocks:
            admin_client.delete(url)
        mocks["regen"].assert_called_once_with(IDR_ID_UUID)
        mocks["dismiss"].assert_not_called()

    def test_delete_auto_general_sets_dismissed(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{GEN_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_AUTO_GEN) as mocks:
            admin_client.delete(url)
        mocks["dismiss"].assert_called_once_with(IDR_ID_UUID)
        mocks["regen"].assert_not_called()

    def test_delete_inspector_general_does_nothing(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{GEN_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_INSPECTOR_GEN) as mocks:
            admin_client.delete(url)
        mocks["regen"].assert_not_called()
        mocks["dismiss"].assert_not_called()

    def test_delete_addendum_regenerates(self, admin_client):
        url = f"/v1/idrs/{IDR_ID}/reports/{ADDENDUM_REPORT_ID}"
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=DELETED_ADDENDUM) as mocks:
            admin_client.delete(url)
        mocks["regen"].assert_called_once_with(IDR_ID_UUID)
        mocks["dismiss"].assert_not_called()


# ---------------------------------------------------------------------------
# Demo users on the IDR endpoints: their own drafts only, and no submitting
# ---------------------------------------------------------------------------

DEMO_IDR_ROW = {**MOCK_IDR_ROW, "project_id": "DEMO01", "reporter_uuid": DEMO_USER_ROW["uuid"]}


class TestDemoUserIdrs:
    list_url = "/v1/idrs/"
    submit_url = f"/v1/idrs/{IDR_ID}/submit"

    def test_submit_is_disabled_for_a_demo_user(self, demo_client):
        with patched(idrs=[DEMO_IDR_ROW]) as mocks:
            response = demo_client.post(self.submit_url)
        assert response.status_code == 403
        assert response.json() == {"detail": "Demo mode: submit is disabled"}
        mocks["idr_reports"].assert_not_called()
        assert all("UPDATE" not in call.args[0] for call in mocks["idrs"].call_args_list)  # nothing was submitted

    def test_submit_still_works_for_everyone_else(self, admin_client):
        with patched(idrs=DRAFT_THEN_SUBMITTED_IDR, idr_reports=REPORTS_THEN_NUMBERED):
            response = admin_client.post(self.submit_url)
        assert response.status_code == 200 and response.json()["data"]["status"] == "submitted"

    def test_a_demo_user_lists_only_their_own_idrs(self, demo_client):
        for params in ({}, {"reporter_uuid": REPORTER_UUID}, {"reporter_uuid": str(ADMIN_USER_ROW["uuid"])}):
            with patched(idrs=[]) as mocks:
                response = demo_client.get(self.list_url, params=params)
            assert response.status_code == 200
            sql, query_params = mocks["idrs"].call_args.args
            assert "i.reporter_uuid = %s" in sql and query_params == (DEMO_USER_ROW["uuid"],)

    def test_a_demo_users_other_filters_still_apply(self, demo_client):
        with patched(idrs=[]) as mocks:
            demo_client.get(self.list_url, params={"project_id": "DEMO01", "status": "draft"})
        assert mocks["idrs"].call_args.args[1] == ("DEMO01", "draft", DEMO_USER_ROW["uuid"])

    def test_everyone_else_keeps_the_optional_reporter_filter(self, admin_client):
        with patched(idrs=[]) as mocks:
            admin_client.get(self.list_url)
        assert "i.reporter_uuid = %s" not in mocks["idrs"].call_args.args[0]
        with patched(idrs=[]) as mocks:
            admin_client.get(self.list_url, params={"reporter_uuid": REPORTER_UUID})
        assert mocks["idrs"].call_args.args[1] == (UUID(REPORTER_UUID),)

    def test_a_demo_user_can_create_a_draft_on_their_project(self, demo_client):
        with patched(idrs=[DEMO_IDR_ROW], projects=ASSIGNED) as mocks:
            response = demo_client.post("/v1/idrs/", json={"project_id": "DEMO01", "report_date": "2026-09-25"})
        assert response.status_code == 201
        assert mocks["idrs"].call_args.args[1][:2] == ("DEMO01", DEMO_USER_ROW["uuid"])

    def test_a_demo_user_can_read_and_edit_their_own_draft(self, demo_client):
        with patched(idrs=[DEMO_IDR_ROW], idr_reports=[]):
            assert demo_client.get(f"/v1/idrs/{IDR_ID}").status_code == 200
        with patched(idrs=[DEMO_IDR_ROW]):
            assert demo_client.put(f"/v1/idrs/{IDR_ID}/header", json={"weather_am": "Clear"}).status_code == 200


# ---------------------------------------------------------------------------
# POST /v1/idrs/{idr_id}/submit: the signature
# ---------------------------------------------------------------------------

MOCK_SIGNED_IDR_ROW = {**MOCK_JUST_SUBMITTED_ROW, "inspector_signature_path": SIGNATURE_COPY, "inspector_signed_at": NOW}


class TestSubmitSignature:
    url = f"/v1/idrs/{IDR_ID}/submit"

    def test_a_user_without_a_signature_cant_submit(self, unsigned_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            response = unsigned_client.post(self.url)
        assert response.status_code == 400
        assert response.json() == {"detail": "Signature required before submitting"}
        mocks["signature"].assert_not_called()
        assert mocks["idrs"].call_count == 1  # the lookup only: no submit statement, the IDR stays a draft

    def test_the_signers_current_signature_is_copied_to_the_idr_before_the_submit(self, admin_client):
        order = []
        with patched(idrs=([MOCK_IDR_ROW], [MOCK_SIGNED_IDR_ROW]), idr_reports=REPORTS_THEN_NUMBERED) as mocks:
            mocks["signature"].side_effect = lambda *args: order.append("copy") or SIGNATURE_COPY
            mocks["idrs"].side_effect = lambda *args: (order.append("query"), [MOCK_IDR_ROW] if len(order) == 1
                                                       else [MOCK_SIGNED_IDR_ROW])[1]
            response = admin_client.post(self.url)
        assert response.status_code == 200
        mocks["signature"].assert_called_once_with(ADMIN_USER_ROW["signature_path"], UUID(IDR_ID))
        assert order == ["query", "copy", "query"]  # load the IDR, copy the file, then the one submit statement

    def test_the_submit_statement_stamps_the_copy_and_the_time(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW], [MOCK_SIGNED_IDR_ROW]), idr_reports=REPORTS_THEN_NUMBERED) as mocks:
            admin_client.post(self.url)
        sql, params = mocks["idrs"].call_args.args
        assert "inspector_signature_path = %s" in sql and "inspector_signed_at = now()" in sql
        assert "status = 'submitted'" in sql and sql.count("UPDATE icid.idrs") == 1  # still one statement
        returning = sql.split("RETURNING")[1]
        assert "inspector_signature_path" in returning and "inspector_signed_at" in returning
        assert params == (UUID(IDR_ID), SIGNATURE_COPY)

    def test_the_response_carries_the_signature_and_when_it_was_signed(self, admin_client):
        with patched(idrs=([MOCK_IDR_ROW], [MOCK_SIGNED_IDR_ROW]), idr_reports=REPORTS_THEN_NUMBERED):
            idr = admin_client.post(self.url).json()["data"]
        assert idr["status"] == "submitted"
        assert idr["inspector_signature_path"] == SIGNATURE_COPY
        assert idr["inspector_signed_at"] == "2026-09-25T15:30:00Z"

    def test_a_failed_copy_is_502_and_the_idr_stays_a_draft(self, admin_client):
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            mocks["signature"].side_effect = SignatureStorageError("Could not copy the signature for this IDR")
            response = admin_client.post(self.url)
        assert response.status_code == 502
        assert response.json() == {"detail": "Could not copy the signature for this IDR"}
        assert mocks["idrs"].call_count == 1  # no submit statement

    def test_a_failed_submit_after_the_copy_is_500_and_the_orphan_is_logged(self, admin_client, caplog):
        with patched(idrs=([MOCK_IDR_ROW], None), idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            response = admin_client.post(self.url)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to submit IDR"}
        mocks["signature"].assert_called_once()
        assert SIGNATURE_COPY in caplog.text and "left orphaned" in caplog.text

    def test_a_submit_that_raises_after_the_copy_logs_the_orphan_too(self, caplog):
        from starlette.testclient import TestClient
        from api.index import app
        from api.services.auth import require_full_user
        from api.schemas.auth import UserOut
        app.dependency_overrides[require_full_user] = lambda: UserOut.model_validate(ADMIN_USER_ROW)
        try:
            with patch("api.services.auth.get_user_by_uuid", return_value=ADMIN_USER_ROW), \
                    patched(idrs=([MOCK_IDR_ROW], RuntimeError("db down")), idr_reports=[MOCK_GEN_REPORT_ROW]), \
                    TestClient(app, raise_server_exceptions=False) as client:
                from tests.v1.test_auth import bearer, token
                response = client.post(self.url, headers=bearer(token(ADMIN_USER_ROW["uuid"])))
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == 500
        assert SIGNATURE_COPY in caplog.text and "left orphaned" in caplog.text

    def test_a_lost_race_after_the_copy_is_409_and_the_orphan_is_logged(self, admin_client, caplog):
        with patched(idrs=([MOCK_IDR_ROW], []), idr_reports=[MOCK_GEN_REPORT_ROW]):
            response = admin_client.post(self.url)
        assert response.status_code == 409
        assert SIGNATURE_COPY in caplog.text and "left orphaned" in caplog.text

    def test_nothing_is_copied_for_an_idr_that_cant_be_submitted(self, admin_client):
        # a submitted IDR's signature must never be touched; nor is a copy made for a missing or empty IDR
        for setup in ({"idrs": [MOCK_SUBMITTED_IDR_ROW]}, {"idrs": []}, {"idrs": [MOCK_IDR_ROW], "idr_reports": []}):
            with patched(**setup) as mocks:
                assert admin_client.post(self.url).status_code in (400, 404, 409)
            mocks["signature"].assert_not_called()

    def test_a_demo_user_is_refused_before_any_of_it(self, demo_client):
        with patched(idrs=[DEMO_IDR_ROW], idr_reports=[MOCK_GEN_REPORT_ROW]) as mocks:
            response = demo_client.post(self.url)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: submit is disabled"}
        mocks["signature"].assert_not_called()

    def test_idr_reads_carry_the_signature_columns(self, admin_client):
        with patched(idrs=[MOCK_SIGNED_IDR_ROW], idr_reports=[]) as mocks:
            idr = admin_client.get(f"/v1/idrs/{IDR_ID}").json()["data"]
        assert "inspector_signature_path" in mocks["idrs"].call_args.args[0]
        assert (idr["inspector_signature_path"], idr["inspector_signed_at"]) == (SIGNATURE_COPY, "2026-09-25T15:30:00Z")
        with patched(idrs=[MOCK_IDR_ROW], idr_reports=[]):
            draft = admin_client.get(f"/v1/idrs/{IDR_ID}").json()["data"]
        assert (draft["inspector_signature_path"], draft["inspector_signed_at"]) == (None, None)
