import io
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import openpyxl
import pytest

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/assessment-summary.xlsx
# ---------------------------------------------------------------------------

SUMMARY = "api.queries.assessment_summary.run_query"
PROJECTS = "api.queries.projects.run_query"
URL = "/v1/projects/SE384/assessment-summary.xlsx"
SITES_URL = "/v1/projects/SE384/assessment-summary"
TAILORED_URL = "/v1/projects/SE384/assessment-summary/tailored.xlsx"
PROJECT_ROW = {"project_id": "SE384", "project_name": "Sidewalks Queens", "project_description": None,
               "registration_code": "2024123456", "borough": "Queens", "status": None,
               "cost_per_sf_4in": Decimal("11.54"), "cost_per_sf_7in": Decimal("12.64")}
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
FLAG = {"idr_id": UUID("1d000000-0000-4000-8000-000000000001"),
        "re_signed_at": datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc),
        "property_id": UUID("20000000-0000-4000-8000-000000000001"), "property_kind": "sidewalk",
        "address": "138-22 227 STREET", "intersection_street_1": None, "intersection_street_2": None,
        "quadrant": None, "facing_street": None, "date_constructed": None, "surveyed_thickness": "4in",
        "surveyed_is_assessable": True, "computed_quantity": Decimal("160.69"), "quantity": Decimal("113.19")}


def projects_for(on_project: bool = True):
    """
    Answer the project statements: the project itself, and whether the user is on it.
    Takes whether they are.
    Returns the side effect.
    """
    def answer(sql, params=None):
        if "FROM icid.project_users" in sql:
            return [{"?column?": 1}] if on_project else []
        return [PROJECT_ROW]
    return answer


def book(response):
    """
    Open a response's workbook.
    Takes the response.
    Returns the openpyxl workbook.
    """
    return openpyxl.load_workbook(io.BytesIO(response.content))


class TestAssessmentSummary:
    def test_it_is_an_xlsx_named_for_the_project_and_today(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        disposition = response.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="SE384_assessment_summary_') and disposition.endswith('.xlsx"')
        assert book(response).sheetnames == ["Oct 2026", "Project Total"]
        sql, params = run.call_args.args
        assert params == ("SE384",)
        assert "i.status = 'approved' AND i.deleted_at IS NULL" in sql
        assert "pg1.page_number = 1" in sql

    def test_a_range_keeps_its_months(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG]):
            assert book(admin_client.get(URL, params={"from": "2026-10", "to": "2026-10"})).sheetnames == ["Oct 2026", "Project Total"]
            later = book(admin_client.get(URL, params={"from": "2026-11"}))
            earlier = book(admin_client.get(URL, params={"to": "2026-09"}))
        assert later.sheetnames == earlier.sheetnames == ["No activity", "Project Total"]
        assert later["No activity"]["A4"].value == "No approved IDRs in the selected range."

    def test_a_project_with_nothing_approved_is_a_no_activity_workbook(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 200
        assert book(response)["No activity"]["A4"].value == "No approved IDRs in this project yet."

    def test_from_after_to_is_400(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY) as run:
            response = admin_client.get(URL, params={"from": "2026-11", "to": "2026-10"})
        assert response.status_code == 400 and response.json() == {"detail": "from after to"}
        run.assert_not_called()

    @pytest.mark.parametrize("name, value", [("from", "10-2026"), ("to", "2026-13")])
    def test_a_month_that_isnt_yyyy_mm_is_400(self, admin_client, name, value):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY) as run:
            response = admin_client.get(URL, params={name: value})
        assert response.status_code == 400 and response.json() == {"detail": f"{name} must be YYYY-MM"}
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=None):
            assert admin_client.get(URL).status_code == 500

    def test_an_unknown_project_is_404(self, admin_client):
        with patch(PROJECTS, return_value=[]), patch(SUMMARY) as run:
            assert admin_client.get(URL).status_code == 404
        run.assert_not_called()

    def test_a_user_on_the_project_downloads_it(self):
        with signed_in(INSPECTOR_ROW) as client, patch(PROJECTS, side_effect=projects_for()), \
                patch(SUMMARY, return_value=[FLAG]):
            assert client.get(URL).status_code == 200

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as client, patch(PROJECTS, side_effect=projects_for(on_project=False)), \
                patch(SUMMARY) as run:
            assert client.get(URL).status_code == 403
        run.assert_not_called()

    def test_an_admin_downloads_any_projects(self, admin_client):
        # admin_client is on no project: project_member lets an admin through
        with patch(PROJECTS, side_effect=projects_for(on_project=False)), patch(SUMMARY, return_value=[FLAG]):
            assert admin_client.get(URL).status_code == 200

    def test_a_demo_user_downloads_their_own_projects(self, demo_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[]):
            assert demo_client.get("/v1/projects/DEMO01/assessment-summary.xlsx").status_code == 200

    def test_a_demo_user_gets_404_for_another_project(self, demo_client):
        with patch(PROJECTS, side_effect=projects_for(on_project=False)), patch(SUMMARY) as run:
            assert demo_client.get(URL).status_code == 404
        run.assert_not_called()


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/assessment-summary (the page's sites)
# ---------------------------------------------------------------------------

ELM_FLAG = {**FLAG, "property_id": UUID("20000000-0000-4000-8000-000000000002"), "address": "350 Main St, Brooklyn",
            "community_board": "BK10", "block": "1234", "lot": "51", "violation_no": "V-77",
            "date_constructed": date(2026, 11, 3)}


class TestAssessmentSites:
    def test_a_row_per_site_with_its_figures_and_the_totals(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG, ELM_FLAG]):
            response = admin_client.get(SITES_URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["range"] == {"from": None, "to": None} and data["cost_per_sf_4in"] == 11.54
        assert [site["display_address"] for site in data["sites"]] == ["138-22 227 STREET", "350 Main St, Brooklyn"]
        elm = data["sites"][1]
        assert (elm["community_board"], elm["block"], elm["lot"], elm["violation_no"], elm["kind"]) == (
            "BK10", "1234", "51", "V-77", "sidewalk")
        assert elm["four_assessable"] == 113.19 and elm["four_amount"] == 1306.21
        assert data["totals"]["four_assessable"] == 226.38 and data["totals"]["seven_amount"] == 0.0

    def test_the_range_is_of_days(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG, ELM_FLAG]):
            data = admin_client.get(SITES_URL, params={"from": "2026-11-01", "to": "2026-11-30"}).json()["data"]
        assert data["range"] == {"from": "2026-11-01", "to": "2026-11-30"}
        assert [site["display_address"] for site in data["sites"]] == ["350 Main St, Brooklyn"]

    @pytest.mark.parametrize("params, detail", [
        ({"from": "2026-11"}, "from must be a date, YYYY-MM-DD"),
        ({"from": "2026-11-02", "to": "2026-11-01"}, "from cannot be after to")])
    def test_a_bad_range_is_400(self, admin_client, params, detail):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY) as run:
            response = admin_client.get(SITES_URL, params=params)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=projects_for(on_project=False)), \
                patch(SUMMARY, return_value=[FLAG]):
            assert inspector.get(SITES_URL).status_code == 403

    def test_a_failed_read_is_500(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=None):
            assert admin_client.get(SITES_URL).status_code == 500


class TestTailoredDownload:
    def test_it_is_one_tab_of_the_sites_asked_for(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG, ELM_FLAG]):
            response = admin_client.get(TAILORED_URL, params={
                "from": "2026-10-01", "to": "2026-11-30", "property_id": str(ELM_FLAG["property_id"])})
        assert response.status_code == 200
        assert 'filename="SE384_assessment_summary_tailored_' in response.headers["content-disposition"]
        workbook = book(response)
        assert workbook.sheetnames == ["2026-10-01 to 2026-11-30"]
        sheet = workbook["2026-10-01 to 2026-11-30"]
        assert sheet["A4"].value == "350 Main St, Brooklyn" and sheet["B4"].value == "BK10" and sheet["A5"].value == "Totals"

    def test_without_property_ids_it_holds_every_site(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG, ELM_FLAG]):
            sheet = book(admin_client.get(TAILORED_URL))["All dates"]
        assert sheet["A4"].value == "138-22 227 STREET" and sheet["A5"].value == "350 Main St, Brooklyn"

    def test_a_property_id_that_isnt_a_uuid_is_422(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY, return_value=[FLAG]):
            assert admin_client.get(TAILORED_URL, params={"property_id": "nope"}).status_code == 422

    def test_a_bad_range_is_400(self, admin_client):
        with patch(PROJECTS, side_effect=projects_for()), patch(SUMMARY) as run:
            assert admin_client.get(TAILORED_URL, params={"to": "31-10-2026"}).status_code == 400
        run.assert_not_called()
