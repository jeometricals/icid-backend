from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from tests.conftest import ADMIN_USER_ROW, signed_in
from tests.v1.test_properties import DELETED, PROPERTY_ROW, RAMP_ID, RAMP_ROW

# ---------------------------------------------------------------------------
# /v1/properties/{property_id}/ped-ramp-attributes
# Mock rows are dicts, as run_query returns them: the property for the checks, then the attributes with ramp_type.
# ---------------------------------------------------------------------------

PROPS = "api.queries.properties.run_query"
ATTRS = "api.queries.ped_ramp_attributes.run_query"
PROJECTS = "api.queries.projects.run_query"
URL = f"/v1/properties/{RAMP_ID}/ped-ramp-attributes"
SAVED = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
ROW = {"property_id": RAMP_ID, "ramp_type": "PERPENDICULAR", "ramp_id_label": "R1",
       "ramp_running_slope_pct": Decimal("7.5"), "ramp_cross_slope_pct": Decimal("2.4"), "created_at": SAVED,
       "updated_at": SAVED}
BODY = {"ramp_id_label": " R1 ", "ramp_running_slope_pct": 7.5, "ramp_cross_slope_pct": 2.4}
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
ON_PROJECT = [{"?column?": 1}]


class TestReadingAttributes:
    def test_a_ramps_attributes_come_with_their_warnings(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]), patch(ATTRS, return_value=[ROW]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ramp_type"] == "PERPENDICULAR" and data["ramp_id_label"] == "R1"
        assert [warning["field"] for warning in data["warnings"]] == ["ramp_cross_slope_pct"]
        assert data["warnings"][0]["message"] == "Ramp cross slope 2.4% exceeds 2%"
        assert run.call_args.args[1] == (RAMP_ID,)

    def test_a_ramp_without_attributes_is_404(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]), patch(ATTRS, return_value=[]):
            assert admin_client.get(URL).json() == {"detail": "Ramp attributes not entered"}

    def test_a_sidewalk_has_none(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]), patch(ATTRS) as run:
            response = admin_client.get(URL)
        assert (response.status_code, response.json()) == (400, {"detail": "Only a ped ramp has ramp attributes"})
        run.assert_not_called()

    def test_a_user_off_the_project_is_403(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS, return_value=[RAMP_ROW]), \
             patch(PROJECTS, return_value=[]), patch(ATTRS) as run:
            assert inspector.get(URL).status_code == 403
        run.assert_not_called()


class TestSavingAttributes:
    def test_a_member_saves_them_whole_and_out_of_range_values_are_kept(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS, return_value=[RAMP_ROW]), \
             patch(PROJECTS, return_value=ON_PROJECT), patch(ATTRS, return_value=[ROW]) as run:
            response = inspector.put(URL, json=BODY)
        assert response.status_code == 200 and len(response.json()["data"]["warnings"]) == 1
        sql, params = run.call_args.args
        assert "INSERT INTO icid.ped_ramp_attributes" in sql and "ON CONFLICT (property_id) DO UPDATE" in sql
        assert "p.kind = 'ped_ramp'" in sql
        assert params[0] == "R1" and params[-1] == RAMP_ID and len(params) == 50
        assert Decimal("7.5") in params and params.count(None) == 46

    def test_a_field_of_another_ramp_type_is_422(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]), patch(ATTRS) as run:
            response = admin_client.put(URL, json={**BODY, "bottom_landing_width_in": 50, "cut_through_length_in": 9})
        assert response.status_code == 422
        assert response.json() == {
            "detail": "a PERPENDICULAR ramp doesn't take bottom_landing_width_in, cut_through_length_in"}
        run.assert_not_called()

    def test_a_cut_through_takes_its_own_fields(self, admin_client):
        cut = {**RAMP_ROW, "ramp_type": "CUT_THROUGH"}
        with patch(PROPS, return_value=[cut]), \
             patch(ATTRS, return_value=[{**ROW, "ramp_type": "CUT_THROUGH"}]):
            assert admin_client.put(URL, json={"cut_through_length_in": 96, "ramp_width_in": 60}).status_code == 200
            assert admin_client.put(URL, json={"landing_width_in": 48}).status_code == 422

    def test_bad_values_are_422(self, admin_client):
        for body in ({"ramp_width_in": -1}, {"dws_color": "blue"}, {"comments": "x" * 2001}, {"ramp_slope": 1},
                     {"ramp_running_slope_pct": "NaN"}):
            with patch(PROPS, return_value=[RAMP_ROW]), patch(ATTRS) as run:
                assert admin_client.put(URL, json=body).status_code == 422
            run.assert_not_called()

    def test_a_demo_user_cant_save(self, demo_client):
        with patch(PROPS, return_value=[{**RAMP_ROW, "project_id": "DEMO01"}]), \
             patch(PROJECTS, return_value=ON_PROJECT), patch(ATTRS) as run:
            assert demo_client.put(URL, json=BODY).status_code == 403
        run.assert_not_called()

    def test_a_deleted_ramp_is_the_admins(self):
        with signed_in(INSPECTOR_ROW) as inspector, \
             patch(PROPS, return_value=[{**RAMP_ROW, "deleted_at": DELETED}]), \
             patch(PROJECTS, return_value=ON_PROJECT), patch(ATTRS) as run:
            response = inspector.put(URL, json=BODY)
        assert (response.status_code, response.json()) == (400, {"detail": "Property is deleted"})
        run.assert_not_called()


class TestRemovingAttributes:
    def test_they_are_removed(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]), patch(ATTRS, return_value=[{"property_id": RAMP_ID}]) as run:
            response = admin_client.delete(URL)
        assert response.status_code == 204
        assert "DELETE FROM icid.ped_ramp_attributes" in run.call_args.args[0]

    def test_none_to_remove_is_404(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]), patch(ATTRS, return_value=[]):
            assert admin_client.delete(URL).status_code == 404
