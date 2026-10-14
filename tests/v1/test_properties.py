from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.properties import get_property_by_bbl, list_properties
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/projects/{project_id}/properties and /v1/properties/{property_id}
# Mock rows are dicts, as run_query returns them: a property's own columns, and a project's for the checks.
# ---------------------------------------------------------------------------

PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
LIST_URL = "/v1/projects/HWS0023/properties"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
OTHER_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e60")
URL = f"/v1/properties/{PROPERTY_ID}"
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

PROJECT_ROW = {"project_id": "HWS0023", "project_name": "S/W Queens 2025", "project_description": None,
               "registration_code": None, "borough": "Queens", "status": None}
PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
DELETED_ROW = {**PROPERTY_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID, "updated_at": DELETED}
OTHER_ROW = {**PROPERTY_ROW, "id": OTHER_ID, "bbl": "4012340057", "address": "12-36 56th Ave"}
BODY = {"bbl": "4012340056", "address": "12-34 56th Ave", "property_type": "1_3_family"}

# A regular user, on the project or not; and an inspector on another project, for the per-property checks
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})
ON_PROJECT = [[PROJECT_ROW], [{"?column?": 1}]]  # get_project_by_id, then is_user_on_project
OFF_PROJECT = [[PROJECT_ROW], []]


def out(row: dict) -> dict:
    """
    Render a property row as the API returns it.
    Takes the row.
    Returns the JSON dict.
    """
    return {key: (value.isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else
                  str(value) if isinstance(value, UUID) else value) for key, value in row.items()}


def same(response_data: dict, row: dict) -> bool:
    """
    Compare a returned property with a row, datetimes by value.
    Takes the response's data and the row.
    Returns True when they match.
    """
    return {key: response_data[key] for key in ("id", "project_id", "bbl", "address", "property_type", "notes",
                                                "created_by", "deleted_by")} == \
        {key: out(row)[key] for key in ("id", "project_id", "bbl", "address", "property_type", "notes",
                                        "created_by", "deleted_by")} and \
        (response_data["deleted_at"] is None) == (row["deleted_at"] is None)


class TestCreateProperty:
    def test_an_admin_creates_a_property_as_its_creator(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "bbl": "  4012340056 ", "notes": "  "})
        assert response.status_code == 201 and same(response.json()["data"], PROPERTY_ROW)
        assert response.json()["data"]["created_by"] == str(ADMIN_ID)
        sql, params = run.call_args.args
        assert "INSERT INTO icid.properties" in sql and "ON CONFLICT DO NOTHING" in sql
        assert params == ("HWS0023", "4012340056", "12-34 56th Ave", "1_3_family", None, ADMIN_ID)

    def test_notes_are_kept_trimmed(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            admin_client.post(LIST_URL, json={**BODY, "notes": " corner lot "})
        assert run.call_args.args[1][4] == "corner lot"

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(PROPS) as run:
            response = inspector.post(LIST_URL, json=BODY)
        assert (response.status_code, response.json()) == FORBIDDEN
        projects.assert_not_called()
        run.assert_not_called()

    def test_a_duplicate_bbl_in_the_project_is_409(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, side_effect=[[], [OTHER_ROW]]) as run:
            response = admin_client.post(LIST_URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": "A property with this BBL already exists in this project",
                                   "existing_property_id": str(OTHER_ID)}
        assert run.call_args.args[1] == ("HWS0023", "4012340056")

    def test_the_same_bbl_in_another_project_is_created(self, admin_client):
        elsewhere = {**PROPERTY_ROW, "project_id": "HWK2026"}
        with patch(PROJECTS, return_value=[{**PROJECT_ROW, "project_id": "HWK2026"}]), \
                patch(PROPS, return_value=[elsewhere]) as run:
            response = admin_client.post("/v1/projects/HWK2026/properties", json=BODY)
        assert response.status_code == 201 and response.json()["data"]["project_id"] == "HWK2026"
        assert run.call_args.args[1][:2] == ("HWK2026", "4012340056")

    @pytest.mark.parametrize("field", ["bbl", "address"])
    def test_a_blank_bbl_or_address_is_400(self, admin_client, field):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, field: "   "})
        assert response.status_code == 400 and response.json() == {"detail": f"{field} cannot be blank"}
        run.assert_not_called()

    def test_an_over_long_address_is_400(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "address": "x" * 201})
        assert response.status_code == 400
        run.assert_not_called()

    def test_an_unknown_property_type_is_422(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "property_type": "mansion"})
        assert response.status_code == 422
        run.assert_not_called()

    def test_an_unknown_project_is_404(self, admin_client):
        with patch(PROJECTS, return_value=[]), patch(PROPS) as run:
            response = admin_client.post("/v1/projects/NOPE/properties", json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"}
        run.assert_not_called()


class TestListProperties:
    def test_an_admin_gets_the_live_properties_by_default(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.get(LIST_URL)
        assert response.status_code == 200 and len(response.json()["data"]) == 1
        sql, params = run.call_args.args
        assert "FROM icid.properties p" in sql and "(%s OR p.deleted_at IS NULL)" in sql
        assert "ORDER BY p.address, p.bbl;" in sql
        assert params == ("HWS0023", False)

    def test_an_admin_can_ask_for_the_deleted_ones_too(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(PROPS, return_value=[PROPERTY_ROW, DELETED_ROW]) as run:
            response = admin_client.get(LIST_URL, params={"include_deleted": "true"})
        assert response.status_code == 200 and len(response.json()["data"]) == 2
        assert response.json()["data"][1]["deleted_at"] is not None
        assert run.call_args.args[1] == ("HWS0023", True)

    def test_include_deleted_from_a_user_who_isnt_an_admin_is_ignored(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=ON_PROJECT), \
                patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = inspector.get(LIST_URL, params={"include_deleted": "true"})
        assert response.status_code == 200
        assert run.call_args.args[1] == ("HWS0023", False)

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=OFF_PROJECT), patch(PROPS) as run:
            response = inspector.get(LIST_URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_cant_see_another_project(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PROPS) as run:
            assert demo_client.get(LIST_URL).status_code == 404
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=None):
            response = admin_client.get(LIST_URL)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to fetch properties"}


class TestGetProperty:
    def test_a_user_on_its_project_reads_it(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = inspector.get(URL)
        assert response.status_code == 200 and same(response.json()["data"], PROPERTY_ROW)

    def test_an_admin_reads_a_deleted_one(self, admin_client):
        with patch(PROPS, return_value=[DELETED_ROW]):
            response = admin_client.get(URL)
        assert response.status_code == 200 and response.json()["data"]["deleted_at"] is not None

    def test_a_user_who_isnt_on_its_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = inspector.get(URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}

    def test_a_demo_user_gets_404_for_another_projects(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PROPS, return_value=[PROPERTY_ROW]):
            response = demo_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}


class TestUpdateProperty:
    def test_an_admin_changes_some_fields(self, admin_client):
        changed = {**PROPERTY_ROW, "address": "12-34 56th Avenue", "property_type": "expedited"}
        with patch(PROPS, return_value=[changed]) as run:
            response = admin_client.patch(URL, json={"address": " 12-34 56th Avenue ", "property_type": "expedited"})
        assert response.status_code == 200 and same(response.json()["data"], changed)
        sql, params = run.call_args.args
        assert "UPDATE icid.properties p" in sql and "updated_at = now()" in sql
        assert "other.deleted_at IS NULL" in sql and "p.deleted_at IS NOT NULL" in sql
        assert params == (None, "12-34 56th Avenue", "expedited", False, None, PROPERTY_ID, None)

    def test_null_notes_clear_them(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            assert admin_client.patch(URL, json={"notes": None}).status_code == 200
        assert run.call_args.args[1][3:5] == (True, None)

    def test_an_unknown_property_type_is_422(self, admin_client):
        with patch(PROPS) as run:
            response = admin_client.patch(URL, json={"property_type": "castle"})
        assert response.status_code == 422
        run.assert_not_called()

    def test_nothing_to_change_is_400(self, admin_client):
        with patch(PROPS) as run:
            response = admin_client.patch(URL, json={"bbl": None})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        run.assert_not_called()

    def test_a_blank_bbl_is_400(self, admin_client):
        with patch(PROPS) as run:
            assert admin_client.patch(URL, json={"bbl": " "}).status_code == 400
        run.assert_not_called()

    def test_a_bbl_another_property_of_the_project_has_is_409(self, admin_client):
        with patch(PROPS, side_effect=[[], [PROPERTY_ROW], [OTHER_ROW]]) as run:
            response = admin_client.patch(URL, json={"bbl": "4012340057"})
        assert response.status_code == 409
        assert response.json()["existing_property_id"] == str(OTHER_ID)
        assert run.call_args.args[1] == ("HWS0023", "4012340057")

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, side_effect=[[], []]):
            response = admin_client.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS) as run:
            response = inspector.patch(URL, json={"address": "1 Main St"})
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()


class TestSoftDeleteProperty:
    def test_an_admin_soft_deletes_it(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [DELETED_ROW]]) as run:
            response = admin_client.post(f"{URL}/admin/soft-delete")
        assert response.status_code == 200 and response.json()["message"] == "Property deleted"
        assert response.json()["data"]["deleted_at"] is not None
        assert response.json()["data"]["deleted_by"] == str(ADMIN_ID)
        sql, params = run.call_args.args
        assert "SET deleted_at = now()" in sql and "p.deleted_at IS NULL" in sql
        assert params == (ADMIN_ID, PROPERTY_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(PROPS, return_value=[DELETED_ROW]) as run:
            response = admin_client.post(f"{URL}/admin/soft-delete")
        assert response.status_code == 200 and response.json()["message"] == "Property was already deleted"
        assert run.call_count == 1  # read, nothing written

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]):
            assert admin_client.post(f"{URL}/admin/soft-delete").status_code == 404

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS) as run:
            response = inspector.post(f"{URL}/admin/soft-delete")
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()


class TestUndeleteProperty:
    def test_an_admin_brings_it_back(self, admin_client):
        with patch(PROPS, side_effect=[[DELETED_ROW], [PROPERTY_ROW]]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 200 and response.json()["message"] == "Property restored"
        assert response.json()["data"]["deleted_at"] is None and response.json()["data"]["deleted_by"] is None
        sql = run.call_args.args[0]
        assert "SET deleted_at = NULL" in sql and "deleted_by = NULL" in sql and "NOT EXISTS" in sql

    def test_one_that_isnt_deleted_is_404(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 404 and response.json() == {"detail": "Property is not deleted"}
        assert run.call_count == 1

    def test_its_bbl_taken_meanwhile_is_409(self, admin_client):
        taker = {**OTHER_ROW, "bbl": PROPERTY_ROW["bbl"]}
        with patch(PROPS, side_effect=[[DELETED_ROW], [], [taker]]):
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 409
        assert response.json() == {"detail": "Another property in this project now has this BBL",
                                   "existing_property_id": str(OTHER_ID)}

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]):
            assert admin_client.post(f"{URL}/admin/undelete").status_code == 404

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS) as run:
            response = inspector.post(f"{URL}/admin/undelete")
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()


class TestPropertyQueries:
    def test_every_table_is_in_the_icid_schema(self):
        with patch(PROPS, return_value=[]) as run:
            list_properties("HWS0023")
            get_property_by_bbl("HWS0023", "4012340056")
        for call in run.call_args_list:
            assert "icid.properties" in call.args[0]
