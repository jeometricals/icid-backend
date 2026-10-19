from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pypdfium2

from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/projects/{project_id}/ped-ramp-corners and /v1/ped-ramp-corners/{corner_id}
# Mock rows are dicts, as run_query returns them: a corner's own columns with its ramps, a project's for the checks.
# ---------------------------------------------------------------------------

CORNERS = "api.queries.ped_ramp_corners.run_query"
RAMPS = "api.queries.ped_ramp_attributes.run_query"
PROJECTS = "api.queries.projects.run_query"
LIST_URL = "/v1/projects/HWS0023/ped-ramp-corners"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
URL = f"/v1/ped-ramp-corners/{CORNER_ID}"
RAMP_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e61")
OTHER_RAMP_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e62")
CREATED = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
PROJECT_ROW = {"project_id": "HWS0023", "project_name": "Sidewalks Demo", "project_description": None,
               "registration_code": None, "borough": "Queens", "status": None}
NOTHING = {field: None for field in (
    "inspected_by", "inspection_date", "entity", "entity_if_other", "dot_corner_id", "corner_number",
    "traffic_control_ns", "traffic_control_ew", "corner_type", "corner_direction", "number_of_ramps",
    "t_intersection", "painted_roadway", "contractor_name", "permittee_id", "fms_id", "permit_number",
    "construction_completion_date", "contract_type", "curb_material", "sidewalk_material", "comments",
)}
RAMP = {"property_id": str(RAMP_ID), "intersection_street_1": "Main St", "intersection_street_2": "1st Ave",
        "quadrant": "SW", "facing_street": "street_2", "ramp_type": "PERPENDICULAR", "ramp_id_label": "R1",
        "has_attributes": True}
CORNER_ROW = {**NOTHING, "corner_id": CORNER_ID, "project_id": "HWS0023", "boro": "Queens",
              "north_south_street": "Main St", "east_west_street": "1st Ave", "corner_direction": "SW",
              "created_at": CREATED, "updated_at": CREATED, "created_by": ADMIN_ID, "deleted_at": None,
              "deleted_by": None, "ramps": [RAMP]}
DELETED_ROW = {**CORNER_ROW, "deleted_at": CREATED, "deleted_by": ADMIN_ID}
BODY = {"boro": "Queens", "north_south_street": " Main St ", "east_west_street": "1st Ave"}
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
ON_PROJECT = [{"?column?": 1}]


class TestListAndCreate:
    def test_a_project_lists_its_corners_with_their_ramps(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS, return_value=[CORNER_ROW]) as run:
            response = admin_client.get(LIST_URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data[0]["corner_id"] == str(CORNER_ID) and data[0]["boro"] == "Queens"
        assert data[0]["ramps"][0]["display_address"] == "Main St & 1st Ave — SW Quadrant (Facing 1st Ave)"
        sql, params = run.call_args.args
        assert "FROM icid.ped_ramp_corners c" in sql and "jsonb_agg" in sql and "p.deleted_at IS NULL" in sql
        assert params == ("HWS0023", False)

    def test_include_deleted_is_an_admins_only(self, admin_token):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=[[PROJECT_ROW], ON_PROJECT]), \
             patch(CORNERS, return_value=[]) as run:
            assert inspector.get(LIST_URL, params={"include_deleted": "true"}).status_code == 200
        assert run.call_args.args[1] == ("HWS0023", False)

    def test_a_user_off_the_project_is_403(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=[[PROJECT_ROW], []]):
            assert inspector.get(LIST_URL).status_code == 403

    def test_a_member_creates_a_corner_trimmed(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS, return_value=[CORNER_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "comments": "  ", "number_of_ramps": 2})
        assert response.status_code == 201
        sql, params = run.call_args.args
        assert "INSERT INTO icid.ped_ramp_corners" in sql and "%s::icid.boro" in sql
        assert params[0] == "HWS0023" and params[-1] == ADMIN_ID
        assert "Main St" in params and None in params and 2 in params

    def test_a_corner_needs_its_borough_and_streets(self, admin_client):
        for missing in ("boro", "north_south_street", "east_west_street"):
            body = {key: value for key, value in BODY.items() if key != missing}
            with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS) as run:
                response = admin_client.post(LIST_URL, json=body)
            assert response.status_code == 422 and f"{missing} is required" in str(response.json())
            run.assert_not_called()
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS) as run:
            assert admin_client.post(LIST_URL, json={**BODY, "east_west_street": "  "}).status_code == 422

    def test_unknown_values_and_long_text_are_422(self, admin_client):
        for extra in ({"corner_type": "roundabout"}, {"number_of_ramps": 4}, {"corner_direction": "NNE"},
                      {"permit_number": "x" * 101}, {"bbl": "1"}):
            with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS) as run:
                assert admin_client.post(LIST_URL, json={**BODY, **extra}).status_code == 422
            run.assert_not_called()

    def test_a_demo_user_cant_create(self, demo_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS) as run:
            response = demo_client.post("/v1/projects/DEMO01/ped-ramp-corners", json=BODY)
        assert response.status_code == 403
        run.assert_not_called()


class TestOneCorner:
    def test_a_member_reads_a_corner(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=ON_PROJECT):
            response = inspector.get(URL)
        assert response.status_code == 200 and response.json()["data"]["ramps"][0]["ramp_id_label"] == "R1"

    def test_an_unknown_corner_is_404(self, admin_client):
        with patch(CORNERS, return_value=[]):
            assert admin_client.get(URL).json() == {"detail": "Corner not found"}

    def test_an_edit_is_merged_and_written_whole(self, admin_client):
        with patch(CORNERS, side_effect=[[CORNER_ROW], [{**CORNER_ROW, "inspected_by": "RG"}]]) as run:
            response = admin_client.patch(URL, json={"inspected_by": " RG ", "north_south_street": None,
                                                     "inspection_date": "2026-10-19"})
        assert response.status_code == 200 and response.json()["data"]["inspected_by"] == "RG"
        sql, params = run.call_args.args
        assert "UPDATE icid.ped_ramp_corners" in sql and params[-1] == CORNER_ID
        # a null on a required field keeps it; the rest are written as merged
        assert "RG" in params and "Main St" in params and date(2026, 10, 19) in params and "SW" in params

    def test_a_null_clears_an_optional_field(self, admin_client):
        with patch(CORNERS, side_effect=[[CORNER_ROW], [CORNER_ROW]]) as run:
            admin_client.patch(URL, json={"corner_direction": None})
        assert "SW" not in run.call_args.args[1]

    def test_an_empty_edit_is_400(self, admin_client):
        with patch(CORNERS, return_value=[CORNER_ROW]):
            assert admin_client.patch(URL, json={}).status_code == 400

    def test_a_deleted_corner_is_the_admins(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[DELETED_ROW]) as run, \
             patch(PROJECTS, return_value=ON_PROJECT):
            response = inspector.patch(URL, json={"inspected_by": "RG"})
        assert (response.status_code, response.json()) == (400, {"detail": "Corner is deleted"})
        assert run.call_count == 1

    def test_only_an_admin_deletes(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]) as run, \
             patch(PROJECTS, return_value=ON_PROJECT):
            assert inspector.delete(URL).status_code == 403
        assert run.call_count == 1

    def test_an_admin_soft_deletes(self, admin_client):
        with patch(CORNERS, side_effect=[[CORNER_ROW], [DELETED_ROW]]) as run:
            response = admin_client.delete(URL)
        assert response.status_code == 200 and response.json()["message"] == "Corner deleted"
        sql, params = run.call_args.args
        assert "deleted_at = now()" in sql and params == (ADMIN_ID, CORNER_ID)

    def test_deleting_again_changes_nothing(self, admin_client):
        with patch(CORNERS, return_value=[DELETED_ROW]) as run:
            response = admin_client.delete(URL)
        assert response.json()["message"] == "Corner was already deleted" and run.call_count == 1


PERPENDICULAR = {"property_id": str(RAMP_ID), "ramp_id_label": "R1", "ramp_running_slope_pct": 9.1,
                 "ramp_width_in": 40, "construction": "new_install", "is_there_ponding": False,
                 "created_at": "2026-10-19T09:00:00+00:00", "updated_at": "2026-10-19T09:00:00+00:00",
                 "ramp_type": "PERPENDICULAR", "comments": "Rebuilt with the corner."}
RAMP_ROWS = [
    {**RAMP, "property_id": RAMP_ID, "attributes": PERPENDICULAR},
    {**RAMP, "property_id": OTHER_RAMP_ID, "ramp_type": "CUT_THROUGH", "ramp_id_label": None, "has_attributes": False,
     "attributes": None},
]


class TestInspectionReport:
    def test_the_report_carries_each_ramp_with_its_warnings(self, admin_client):
        with patch(CORNERS, return_value=[CORNER_ROW]), patch(RAMPS, return_value=RAMP_ROWS) as run:
            response = admin_client.get(f"{URL}/inspection-report")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ramp_types"] == ["PERPENDICULAR", "CUT_THROUGH"]
        first, second = data["ramps"]
        assert {warning["field"] for warning in first["attributes"]["warnings"]} == {
            "ramp_running_slope_pct", "ramp_width_in"}
        assert second["attributes"] is None
        assert run.call_args.args[1] == (CORNER_ID,)

    def test_the_pdf_has_the_corner_page_and_a_page_per_ramp(self, admin_client):
        with patch(CORNERS, return_value=[CORNER_ROW]), patch(RAMPS, return_value=RAMP_ROWS):
            response = admin_client.get(f"{URL}/inspection-report.pdf")
        assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
        assert response.headers["content-disposition"] == \
            'attachment; filename="Main_St_and_1st_Ave_SW_ped_ramp_inspection.pdf"'
        pdf = pypdfium2.PdfDocument(response.content)
        assert len(pdf) == 3
        corner, perpendicular, cut_through = (pdf[index].get_textpage().get_text_range() for index in range(3))
        assert "PEDESTRIAN RAMP INSPECTION REPORT" in corner and "RAMPS AT THIS CORNER (2)" in corner
        assert "1 ADA warning" not in corner and "2 ADA warnings" in corner and "attributes not entered" in corner
        assert "Perpendicular ramp" in perpendicular and "9.1% (ADA)" in perpendicular
        assert "Ramp running slope 9.1% exceeds 8.3%" in perpendicular
        assert "Ramp width 40 in is less than 60 in" in perpendicular
        assert "Cut-through" in cut_through and "Attributes not entered." in cut_through

    def test_a_member_off_the_project_is_403(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(CORNERS, return_value=[CORNER_ROW]), \
             patch(PROJECTS, return_value=[]), patch(RAMPS) as run:
            assert inspector.get(f"{URL}/inspection-report.pdf").status_code == 403
        run.assert_not_called()
