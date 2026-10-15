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
                "kind": "sidewalk", "boro": "Queens", "violation_no": None, "intersection_street_1": None,
                "intersection_street_2": None, "quadrant": None, "facing_street": None, "ramp_type": None,
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
DELETED_ROW = {**PROPERTY_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID, "updated_at": DELETED}
OTHER_ROW = {**PROPERTY_ROW, "id": OTHER_ID, "bbl": "4012340057", "address": "12-36 56th Ave"}
# Rows as the list returns them: with the live PIR's status
NO_STATUS = {"status": {"state": "none", "date": None}, "task_order_no": None}
LISTED_ROW = {**PROPERTY_ROW, "pir_status": "none", **NO_STATUS}
LISTED_DELETED_ROW = {**DELETED_ROW, "pir_status": "dot_signed", **NO_STATUS}
# A ped ramp, as the table holds one
RAMP_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e61")
RAMP_ROW = {**PROPERTY_ROW, "id": RAMP_ID, "kind": "ped_ramp", "boro": "Brooklyn", "bbl": None, "address": None,
            "property_type": None, "intersection_street_1": "Main St", "intersection_street_2": "1st Ave",
            "quadrant": "NE", "facing_street": "street_2", "ramp_type": "PERPENDICULAR"}
RAMP_BODY = {"kind": "ped_ramp", "boro": "Brooklyn", "intersection_street_1": "Main St",
             "intersection_street_2": "1st Ave", "quadrant": "NE", "facing_street": "street_2",
             "ramp_type": "PERPENDICULAR"}
TZ = "America/New_York"
SIDEWALK_NOTHING_ELSE = (None,) * 6  # violation_no and the five ped-ramp columns
BODY ={"bbl": "4012340056", "address": "12-34 56th Ave", "property_type": "1_3_family"}

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
        assert params == ("HWS0023", "sidewalk", "Queens", "4012340056", "12-34 56th Ave", "1_3_family",
                          *SIDEWALK_NOTHING_ELSE, None, ADMIN_ID)

    def test_notes_are_kept_trimmed(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            admin_client.post(LIST_URL, json={**BODY, "notes": " corner lot "})
        assert run.call_args.args[1][12] == "corner lot"

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
        assert run.call_args.args[1][0] == "HWK2026" and run.call_args.args[1][3] == "4012340056"

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
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[LISTED_ROW]) as run:
            response = admin_client.get(LIST_URL)
        assert response.status_code == 200 and len(response.json()["data"]) == 1
        sql, params = run.call_args.args
        assert "FROM icid.properties p" in sql and "(%s OR p.deleted_at IS NULL)" in sql
        assert ("ORDER BY COALESCE(p.address, p.intersection_street_1 || ' & ' || p.intersection_street_2), "
                "p.quadrant, p.bbl;") in sql
        assert params == (TZ, "HWS0023", False, None, None)

    def test_an_admin_can_ask_for_the_deleted_ones_too(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(PROPS, return_value=[LISTED_ROW, LISTED_DELETED_ROW]) as run:
            response = admin_client.get(LIST_URL, params={"include_deleted": "true"})
        assert response.status_code == 200 and len(response.json()["data"]) == 2
        assert response.json()["data"][1]["deleted_at"] is not None
        assert run.call_args.args[1] == (TZ, "HWS0023", True, None, None)

    def test_include_deleted_from_a_user_who_isnt_an_admin_is_ignored(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=ON_PROJECT), \
                patch(PROPS, return_value=[LISTED_ROW]) as run:
            response = inspector.get(LIST_URL, params={"include_deleted": "true"})
        assert response.status_code == 200
        assert run.call_args.args[1] == (TZ, "HWS0023", False, None, None)

    @pytest.mark.parametrize("status", ["none", "dot_signed", "as_built_signed"])
    def test_each_row_carries_its_pir_status(self, admin_client, status):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(PROPS, return_value=[{**LISTED_ROW, "pir_status": status}]):
            response = admin_client.get(LIST_URL)
        assert response.json()["data"][0]["pir_status"] == status

    def test_the_status_reads_the_live_pir_only(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[]) as run:
            admin_client.get(LIST_URL)
        one_line = " ".join(run.call_args.args[0].split())
        assert "LEFT JOIN icid.pirs pir ON pir.property_id = p.id AND pir.deleted_at IS NULL" in one_line
        assert ("CASE WHEN pir.id IS NULL THEN 'none' WHEN pir.as_built_signed_at IS NOT NULL THEN 'as_built_signed' "
                "ELSE 'dot_signed' END AS pir_status") in one_line

    def test_the_other_property_responses_have_no_pir_status(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]):
            assert "pir_status" not in admin_client.get(URL).json()["data"]

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
        assert params == ("Queens", "4012340056", "12-34 56th Avenue", "expedited", *SIDEWALK_NOTHING_ELSE, None,
                          PROPERTY_ID, "4012340056")

    def test_null_notes_clear_them(self, admin_client):
        with patch(PROPS, side_effect=[[{**PROPERTY_ROW, "notes": "old"}], [PROPERTY_ROW]]) as run:
            assert admin_client.patch(URL, json={"notes": None}).status_code == 200
        assert run.call_args.args[1][10] is None

    def test_an_unknown_property_type_is_422(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"property_type": "castle"})
        assert response.status_code == 422
        assert not any("UPDATE" in call.args[0] for call in run.call_args_list)

    def test_nothing_to_change_is_400(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"bbl": None})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1  # the property read, nothing written

    def test_a_blank_bbl_is_400(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            assert admin_client.patch(URL, json={"bbl": " "}).status_code == 400
        assert run.call_count == 1

    def test_an_admin_edits_a_deleted_one(self, admin_client):
        with patch(PROPS, return_value=[DELETED_ROW]):
            assert admin_client.patch(URL, json={"notes": "x"}).status_code == 200

    def test_a_bbl_another_property_of_the_project_has_is_409(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [], [PROPERTY_ROW], [OTHER_ROW]]) as run:
            response = admin_client.patch(URL, json={"bbl": "4012340057"})
        assert response.status_code == 409
        assert response.json()["existing_property_id"] == str(OTHER_ID)
        assert run.call_args.args[1] == ("HWS0023", "4012340057")

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, side_effect=[[], []]):
            response = admin_client.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}


class TestProjectMembersWriteProperties:
    """Creating and editing a property are open to anyone on the project; demo users and outsiders stay out."""

    def test_a_member_creates_a_property_as_its_creator(self):
        mine = {**PROPERTY_ROW, "created_by": INSPECTOR_ROW["uuid"]}
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=ON_PROJECT), \
                patch(PROPS, return_value=[mine]) as run:
            response = inspector.post(LIST_URL, json=BODY)
        assert response.status_code == 201
        assert response.json()["data"]["created_by"] == str(INSPECTOR_ROW["uuid"])
        assert run.call_args.args[1][-1] == INSPECTOR_ROW["uuid"]

    def test_a_member_edits_a_property(self):
        changed = {**PROPERTY_ROW, "address": "1 Main St"}
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(PROPS, return_value=[changed]):
            response = inspector.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 200 and response.json()["data"]["address"] == "1 Main St"

    def test_a_member_cant_edit_a_deleted_property(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(PROPS, return_value=[DELETED_ROW]) as run:
            response = inspector.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 400 and response.json() == {"detail": "Property is deleted"}
        assert run.call_count == 1  # read, nothing written

    def test_a_user_who_isnt_on_the_project_cant_create(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=OFF_PROJECT), patch(PROPS) as run:
            response = inspector.post(LIST_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_user_who_isnt_on_the_project_cant_edit(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = inspector.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert run.call_count == 1

    def test_a_demo_user_cant_create_on_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=[{"?column?": 1}]), patch(PROPS) as run:
            response = demo_client.post("/v1/projects/DEMO01/properties", json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: properties are read-only"}
        run.assert_not_called()

    def test_a_demo_user_cant_edit_their_projects_property(self, demo_client):
        with patch(PROJECTS, return_value=[{"?column?": 1}]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = demo_client.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: properties are read-only"}
        assert run.call_count == 1

    def test_a_demo_user_gets_404_on_another_project(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PROPS, return_value=[PROPERTY_ROW]):
            assert demo_client.post(LIST_URL, json=BODY).status_code == 404
            response = demo_client.patch(URL, json={"address": "1 Main St"})
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}

    @pytest.mark.parametrize("action", ["soft-delete", "undelete"])
    def test_a_member_who_isnt_an_admin_cant_delete_or_restore(self, action):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(PROPS) as run:
            response = inspector.post(f"{URL}/admin/{action}")
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
            list_properties("HWS0023", "America/New_York")
            get_property_by_bbl("HWS0023", "4012340056")
        for call in run.call_args_list:
            assert "icid.properties" in call.args[0]



class TestPedRamps:
    def test_a_ped_ramp_is_created_with_its_own_fields_only(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[RAMP_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**RAMP_BODY, "intersection_street_1": " Main St "})
        assert response.status_code == 201
        assert run.call_args.args[1] == ("HWS0023", "ped_ramp", "Brooklyn", None, None, None, None, "Main St",
                                         "1st Ave", "NE", "street_2", "PERPENDICULAR", None, ADMIN_ID)
        sql = run.call_args.args[0]
        assert "%s::icid.property_kind" in sql and "%s::icid.boro" in sql and "%s::icid.quadrant" in sql
        assert "%s::icid.facing_street" in sql and "%s::icid.ramp_type" in sql

    def test_a_ped_ramp_is_labelled_by_its_intersection(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]):
            data = admin_client.get(f"/v1/properties/{RAMP_ID}").json()["data"]
        assert data["display_address"] == "Main St & 1st Ave \u2014 NE Quadrant (Facing 1st Ave)"
        assert data["block_display"] is None and data["lot_display"] is None
        assert data["kind"] == "ped_ramp" and data["bbl"] is None

    def test_facing_street_1_names_the_first_street(self, admin_client):
        with patch(PROPS, return_value=[{**RAMP_ROW, "facing_street": "street_1", "quadrant": "SW"}]):
            data = admin_client.get(f"/v1/properties/{RAMP_ID}").json()["data"]
        assert data["display_address"] == "Main St & 1st Ave \u2014 SW Quadrant (Facing Main St)"

    def test_a_sidewalk_is_labelled_by_its_address_and_shows_its_block_and_lot(self, admin_client):
        with patch(PROPS, return_value=[{**PROPERTY_ROW, "bbl": "1000010001"}]):
            data = admin_client.get(URL).json()["data"]
        assert (data["display_address"], data["block_display"], data["lot_display"]) == ("12-34 56th Ave", "1", "1")

    @pytest.mark.parametrize("missing", ["boro", "intersection_street_1", "intersection_street_2", "quadrant",
                                         "facing_street", "ramp_type"])
    def test_a_ped_ramp_missing_a_field_is_422(self, admin_client, missing):
        body = {key: value for key, value in RAMP_BODY.items() if key != missing}
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json=body)
        assert response.status_code == 422 and missing in response.text
        run.assert_not_called()

    @pytest.mark.parametrize("stray", [{"bbl": "3012340056"}, {"address": "1 Main St"},
                                       {"property_type": "expedited"}, {"violation_no": "V-1"}])
    def test_a_ped_ramp_with_a_sidewalks_field_is_422(self, admin_client, stray):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**RAMP_BODY, **stray})
        assert response.status_code == 422 and "doesn't take" in response.text and next(iter(stray)) in response.text
        run.assert_not_called()

    @pytest.mark.parametrize("stray", [{"intersection_street_1": "Main St"}, {"quadrant": "NE"},
                                       {"facing_street": "street_1"}, {"ramp_type": "PARALLEL"}])
    def test_a_sidewalk_with_a_ped_ramps_field_is_422(self, admin_client, stray):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, **stray})
        assert response.status_code == 422 and "doesn't take" in response.text
        run.assert_not_called()

    def test_a_sidewalk_without_its_bbl_is_422(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={"address": "1 Main St", "property_type": "expedited"})
        assert response.status_code == 422 and "needs bbl" in response.text
        run.assert_not_called()

    @pytest.mark.parametrize("value", ["NW-ish", "north"])
    def test_an_unknown_quadrant_is_422(self, admin_client, value):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            assert admin_client.post(LIST_URL, json={**RAMP_BODY, "quadrant": value}).status_code == 422
        run.assert_not_called()

    def test_a_blank_street_is_400(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**RAMP_BODY, "intersection_street_2": "  "})
        assert response.status_code == 400 and response.json() == {"detail": "intersection_street_2 cannot be blank"}
        run.assert_not_called()


class TestSidewalkBorough:
    def test_a_sidewalks_borough_is_read_off_its_bbl(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            admin_client.post(LIST_URL, json={**BODY, "bbl": "5012340056"})
        assert run.call_args.args[1][1:4] == ("sidewalk", "Staten Island", "5012340056")

    def test_a_borough_sent_that_matches_the_bbl_is_taken(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]):
            assert admin_client.post(LIST_URL, json={**BODY, "boro": "Queens"}).status_code == 201

    def test_a_borough_that_isnt_the_bbls_is_400(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "boro": "Bronx"})
        assert response.status_code == 400 and response.json() == {"detail": "boro must be Queens, the BBL's borough"}
        run.assert_not_called()

    @pytest.mark.parametrize("bbl", ["401234005", "40123400567", "6012340056", "0012340056", "4-01234-0056"])
    def test_a_bbl_that_isnt_ten_digits_starting_1_to_5_is_400(self, admin_client, bbl):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "bbl": bbl})
        assert response.status_code == 400
        assert response.json() == {"detail": "bbl must be 10 digits: the borough (1-5), the block (5) and the lot (4)"}
        run.assert_not_called()

    def test_a_new_bbl_moves_the_borough_with_it(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [PROPERTY_ROW]]) as run:
            assert admin_client.patch(URL, json={"bbl": "3012340056"}).status_code == 200
        assert run.call_args.args[1][:2] == ("Brooklyn", "3012340056")


class TestEditingByKind:
    def test_the_kind_cant_change(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"kind": "ped_ramp"})
        assert response.status_code == 400
        assert response.json() == {"detail": "Property kind cannot be changed. Delete and recreate."}
        assert run.call_count == 1  # read, nothing written

    def test_sending_the_same_kind_alone_is_nothing_to_change(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"kind": "sidewalk"})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1

    def test_a_ped_ramp_is_edited_merged_and_written_whole(self, admin_client):
        with patch(PROPS, side_effect=[[RAMP_ROW], [{**RAMP_ROW, "quadrant": "SE"}]]) as run:
            response = admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"quadrant": "SE", "kind": "ped_ramp"})
        assert response.status_code == 200 and response.json()["data"]["quadrant"] == "SE"
        assert run.call_args.args[1] == ("Brooklyn", None, None, None, None, "Main St", "1st Ave", "SE", "street_2",
                                         "PERPENDICULAR", None, RAMP_ID, None)

    def test_a_sidewalks_field_on_a_ped_ramp_is_422(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]) as run:
            response = admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"bbl": "3012340056"})
        assert response.status_code == 422 and "doesn't take bbl" in response.text
        assert run.call_count == 1

    def test_a_ped_ramps_field_on_a_sidewalk_is_422(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"ramp_type": "PARALLEL"})
        assert response.status_code == 422 and "doesn't take ramp_type" in response.text
        assert run.call_count == 1

    def test_a_null_or_blank_violation_no_clears_it(self, admin_client):
        for value in (None, "  "):
            with patch(PROPS, side_effect=[[{**PROPERTY_ROW, "violation_no": "V-9"}], [PROPERTY_ROW]]) as run:
                assert admin_client.patch(URL, json={"violation_no": value}).status_code == 200
            assert run.call_args.args[1][4] is None

    def test_a_sidewalk_keeps_its_violation_no_trimmed(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [PROPERTY_ROW]]) as run:
            assert admin_client.patch(URL, json={"violation_no": " V-123 "}).status_code == 200
        assert run.call_args.args[1][4] == "V-123"


class TestListStatus:
    def test_the_list_can_be_filtered_by_kind(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[]) as run:
            assert admin_client.get(LIST_URL, params={"kind": "ped_ramp"}).status_code == 200
        sql, params = run.call_args.args
        assert params == (TZ, "HWS0023", False, "ped_ramp", "ped_ramp")
        assert "(%s::icid.property_kind IS NULL OR p.kind = %s::icid.property_kind)" in sql

    def test_an_unknown_kind_is_422(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            assert admin_client.get(LIST_URL, params={"kind": "driveway"}).status_code == 422
        run.assert_not_called()

    @pytest.mark.parametrize("status,task_order_no", [
        ({"state": "none", "date": None}, None),
        ({"state": "task_ordered", "date": None}, "TO-007"),
        ({"state": "constructed_on", "date": "2026-10-09"}, "TO-007"),
    ])
    def test_each_row_carries_its_status_and_latest_task_order(self, admin_client, status, task_order_no):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(PROPS, return_value=[{**LISTED_ROW, "status": status, "task_order_no": task_order_no}]):
            data = admin_client.get(LIST_URL).json()["data"][0]
        assert data["status"] == status and data["task_order_no"] == task_order_no
        assert data["display_address"] == "12-34 56th Ave" and data["block_display"] == "1234"

    def test_the_status_is_worked_out_in_the_statement(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[]) as run:
            admin_client.get(LIST_URL)
        one_line = " ".join(run.call_args.args[0].split())
        assert ("'state', CASE WHEN pir.as_built_signed_at IS NOT NULL THEN 'constructed_on' "
                "WHEN latest.task_order_number IS NOT NULL THEN 'task_ordered' ELSE 'none' END, "
                "'date', (pir.as_built_signed_at AT TIME ZONE %s)::date ) AS status") in one_line
        assert "latest.task_order_number AS task_order_no" in one_line
        assert ("JOIN icid.task_orders t ON t.id = tp.task_order_id WHERE tp.property_id = p.id AND "
                "t.deleted_at IS NULL ORDER BY t.issued_date DESC NULLS LAST, t.created_at DESC LIMIT 1 ) latest ON true"
                ) in one_line

    def test_only_the_list_carries_the_status(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]):
            data = admin_client.get(URL).json()["data"]
        assert "status" not in data and "task_order_no" not in data and "display_address" in data
