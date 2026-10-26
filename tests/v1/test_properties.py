from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import httpx
import pytest

from api.queries.properties import get_sidewalk_at, list_properties, undelete_property
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
PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "block": "1234", "lot": "56", "address": "12-34 56th Ave",
                "kind": "sidewalk", "boro": "Queens", "violation_no": None, "intersection_street_1": None,
                "intersection_street_2": None, "quadrant": None, "facing_street": None, "ramp_type": None,
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
DELETED_ROW = {**PROPERTY_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID, "updated_at": DELETED}
OTHER_ROW = {**PROPERTY_ROW, "id": OTHER_ID, "lot": "57", "address": "12-36 56th Ave"}
# Rows as the list returns them: with the live PIR's status
NO_STATUS = {"status": {"state": "none", "date": None}, "task_order_no": None}
LISTED_ROW = {**PROPERTY_ROW, "pir_status": "none", **NO_STATUS}
LISTED_DELETED_ROW = {**DELETED_ROW, "pir_status": "dot_signed", **NO_STATUS}
# A ped ramp, as the table holds one
RAMP_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e61")
RAMP_ROW = {**PROPERTY_ROW, "id": RAMP_ID, "kind": "ped_ramp", "boro": "Brooklyn", "block": None, "lot": None,
            "address": None,
            "property_type": None, "intersection_street_1": "Main St", "intersection_street_2": "1st Ave",
            "quadrant": "NE", "facing_street": "street_2", "ramp_type": "PERPENDICULAR"}
RAMP_BODY = {"kind": "ped_ramp", "boro": "Brooklyn", "intersection_street_1": "Main St",
             "intersection_street_2": "1st Ave", "quadrant": "NE", "facing_street": "street_2",
             "ramp_type": "PERPENDICULAR"}
TZ = "America/New_York"
SIDEWALK_NOTHING_ELSE = (None,) * 8  # violation_no, the five ped-ramp columns, corner_id and community_board
BODY = {"boro": "Queens", "block": "1234", "lot": "56", "address": "12-34 56th Ave", "property_type": "1_3_family"}
TAKEN = "A site at Queens Block 1234 Lot 56 already exists in this project."

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
    return {key: response_data[key] for key in ("id", "project_id", "block", "lot", "address", "property_type", "notes",
                                                "created_by", "deleted_by")} == \
        {key: out(row)[key] for key in ("id", "project_id", "block", "lot", "address", "property_type", "notes",
                                        "created_by", "deleted_by")} and \
        (response_data["deleted_at"] is None) == (row["deleted_at"] is None)


class TestCreateProperty:
    def test_an_admin_creates_a_property_as_its_creator(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "block": "  01234 ", "notes": "  "})
        assert response.status_code == 201 and same(response.json()["data"], PROPERTY_ROW)
        assert response.json()["data"]["created_by"] == str(ADMIN_ID)
        sql, params = run.call_args.args
        assert "INSERT INTO icid.properties" in sql and "ON CONFLICT DO NOTHING" in sql
        assert params == ("HWS0023", "sidewalk", "Queens", "1234", "56", "12-34 56th Ave", "1_3_family",
                          *SIDEWALK_NOTHING_ELSE, None, ADMIN_ID)

    def test_notes_are_kept_trimmed(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            admin_client.post(LIST_URL, json={**BODY, "notes": " corner lot "})
        assert run.call_args.args[1][15] == "corner lot"

    def test_a_community_board_is_kept_trimmed_and_returned(self, admin_client):
        row = {**PROPERTY_ROW, "community_board": "QN13"}
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[row]) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "community_board": " QN13 "})
        assert response.status_code == 201 and response.json()["data"]["community_board"] == "QN13"
        assert run.call_args.args[1][14] == "QN13" and "p.community_board" in run.call_args.args[0]

    @pytest.mark.parametrize("kind_body", ["sidewalk", "ped_ramp"])
    def test_either_kind_takes_a_community_board(self, admin_client, kind_body):
        body = {**(BODY if kind_body == "sidewalk" else RAMP_BODY), "community_board": "BK02"}
        row = {**(PROPERTY_ROW if kind_body == "sidewalk" else RAMP_ROW), "community_board": "BK02"}
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[row]):
            response = admin_client.post(LIST_URL, json=body)
        assert response.status_code == 201 and response.json()["data"]["community_board"] == "BK02"

    def test_a_community_board_over_ten_characters_is_400(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "community_board": "12345678901"})
        assert response.status_code == 400 and "community_board" in response.json()["detail"]
        assert all("INSERT" not in call.args[0] for call in run.call_args_list)

    def test_a_blank_community_board_is_none(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            admin_client.post(LIST_URL, json={**BODY, "community_board": "  "})
        assert run.call_args.args[1][14] is None

    def test_a_sidewalk_at_a_taken_borough_block_and_lot_is_409(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, side_effect=[[], [OTHER_ROW]]) as run:
            response = admin_client.post(LIST_URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": TAKEN, "existing_property_id": str(OTHER_ID)}
        assert run.call_args.args[1] == ("HWS0023", "Queens", "1234", "56")

    def test_the_same_lot_in_another_project_is_created(self, admin_client):
        elsewhere = {**PROPERTY_ROW, "project_id": "HWK2026"}
        with patch(PROJECTS, return_value=[{**PROJECT_ROW, "project_id": "HWK2026"}]), \
                patch(PROPS, return_value=[elsewhere]) as run:
            response = admin_client.post("/v1/projects/HWK2026/properties", json=BODY)
        assert response.status_code == 201 and response.json()["data"]["project_id"] == "HWK2026"
        assert run.call_args.args[1][0] == "HWK2026" and run.call_args.args[1][3:5] == ("1234", "56")

    @pytest.mark.parametrize("field", ["address"])
    def test_a_blank_address_is_400(self, admin_client, field):
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
                "p.quadrant, p.boro, p.block, p.lot;") in sql
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
        assert params == ("Queens", "1234", "56", "12-34 56th Avenue", "expedited", *SIDEWALK_NOTHING_ELSE, None,
                          "Queens", "1234", "56", PROPERTY_ID, "Queens", "1234", "56")
        one_line = " ".join(sql.split())
        assert ("other.kind = 'sidewalk' AND other.boro = %s::icid.boro AND other.block = %s AND other.lot = %s"
                in one_line)

    def test_null_community_board_clears_it(self, admin_client):
        with patch(PROPS, side_effect=[[{**PROPERTY_ROW, "community_board": "QN13"}], [PROPERTY_ROW]]) as run:
            response = admin_client.patch(URL, json={"community_board": None})
        assert response.status_code == 200 and run.call_args.args[1][12] is None

    def test_a_community_board_is_set_on_its_own(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [{**PROPERTY_ROW, "community_board": "QN13"}]]) as run:
            response = admin_client.patch(URL, json={"community_board": "QN13"})
        assert response.status_code == 200 and response.json()["data"]["community_board"] == "QN13"
        assert run.call_args.args[1][12] == "QN13"

    def test_null_notes_clear_them(self, admin_client):
        with patch(PROPS, side_effect=[[{**PROPERTY_ROW, "notes": "old"}], [PROPERTY_ROW]]) as run:
            assert admin_client.patch(URL, json={"notes": None}).status_code == 200
        assert run.call_args.args[1][12] is None

    def test_an_unknown_property_type_is_422(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"property_type": "castle"})
        assert response.status_code == 422
        assert not any("UPDATE" in call.args[0] for call in run.call_args_list)

    def test_nothing_to_change_is_400(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.patch(URL, json={"block": None})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1  # the property read, nothing written

    def test_a_blank_block_is_422(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            assert admin_client.patch(URL, json={"block": " "}).status_code == 422
        assert run.call_count == 1

    def test_an_admin_edits_a_deleted_one(self, admin_client):
        with patch(PROPS, return_value=[DELETED_ROW]):
            assert admin_client.patch(URL, json={"notes": "x"}).status_code == 200

    def test_a_lot_another_sidewalk_of_the_project_is_at_is_409(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [], [PROPERTY_ROW], [OTHER_ROW]]) as run:
            response = admin_client.patch(URL, json={"lot": "0057"})
        assert response.status_code == 409
        assert response.json() == {"detail": "A site at Queens Block 1234 Lot 57 already exists in this project.",
                                   "existing_property_id": str(OTHER_ID)}
        assert run.call_args.args[1] == ("HWS0023", "Queens", "1234", "57")

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

    def test_its_lot_taken_meanwhile_is_409(self, admin_client):
        taker = {**OTHER_ROW, "lot": PROPERTY_ROW["lot"]}
        with patch(PROPS, side_effect=[[DELETED_ROW], [], [taker]]):
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 409
        assert response.json() == {"detail": "Another site at Queens Block 1234 Lot 56 now exists in this project.",
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
            get_sidewalk_at("HWS0023", "Queens", "1234", "56")
        for call in run.call_args_list:
            assert "icid.properties" in call.args[0]



class TestPedRamps:
    def test_a_ped_ramp_is_created_with_its_own_fields_only(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[RAMP_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**RAMP_BODY, "intersection_street_1": " Main St "})
        assert response.status_code == 201
        assert run.call_args.args[1] == ("HWS0023", "ped_ramp", "Brooklyn", None, None, None, None, None, "Main St",
                                         "1st Ave", "NE", "street_2", "PERPENDICULAR", None, None, None,
                                         ADMIN_ID)
        sql = run.call_args.args[0]
        assert "%s::icid.property_kind" in sql and "%s::icid.boro" in sql and "%s::icid.quadrant" in sql
        assert "%s::icid.facing_street" in sql and "%s::icid.ramp_type" in sql

    def test_a_ped_ramp_is_labelled_by_its_intersection(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]):
            data = admin_client.get(f"/v1/properties/{RAMP_ID}").json()["data"]
        assert data["display_address"] == "Main St & 1st Ave \u2014 NE Quadrant (Facing 1st Ave)"
        assert data["block"] is None and data["lot"] is None
        assert data["kind"] == "ped_ramp" and "bbl" not in data

    def test_facing_street_1_names_the_first_street(self, admin_client):
        with patch(PROPS, return_value=[{**RAMP_ROW, "facing_street": "street_1", "quadrant": "SW"}]):
            data = admin_client.get(f"/v1/properties/{RAMP_ID}").json()["data"]
        assert data["display_address"] == "Main St & 1st Ave \u2014 SW Quadrant (Facing Main St)"

    def test_a_sidewalk_is_labelled_by_its_address_and_shows_its_block_and_lot(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]):
            data = admin_client.get(URL).json()["data"]
        assert (data["display_address"], data["boro"], data["block"], data["lot"]) == ("12-34 56th Ave", "Queens",
                                                                                       "1234", "56")
        assert not {"bbl", "block_display", "lot_display"} & data.keys()

    @pytest.mark.parametrize("missing", ["boro", "intersection_street_1", "intersection_street_2", "quadrant",
                                         "facing_street", "ramp_type"])
    def test_a_ped_ramp_missing_a_field_is_422(self, admin_client, missing):
        body = {key: value for key, value in RAMP_BODY.items() if key != missing}
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json=body)
        assert response.status_code == 422 and missing in response.text
        run.assert_not_called()

    @pytest.mark.parametrize("stray", [{"block": "123"}, {"lot": "4"}, {"address": "1 Main St"},
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

    @pytest.mark.parametrize("missing", ["boro", "block", "lot", "address", "property_type"])
    def test_a_sidewalk_missing_a_field_is_422(self, admin_client, missing):
        body = {key: value for key, value in BODY.items() if key != missing}
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json=body)
        assert response.status_code == 422 and f"needs {missing}" in response.text
        run.assert_not_called()

    def test_the_old_bbl_shape_is_422_for_its_missing_fields(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={"bbl": "4012340056", "address": "1 Main St",
                                                         "property_type": "expedited"})
        assert response.status_code == 422 and "needs boro, block, lot" in response.text
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
        assert run.call_args.args[1] == ("Brooklyn", None, None, None, None, None, "Main St", "1st Ave", "SE",
                                         "street_2", "PERPENDICULAR", None, None, None, "Brooklyn", None, None,
                                         RAMP_ID, "Brooklyn", None, None)

    def test_a_sidewalks_field_on_a_ped_ramp_is_422(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]) as run:
            response = admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"block": "12"})
        assert response.status_code == 422 and "doesn't take block" in response.text
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
            assert run.call_args.args[1][5] is None

    def test_a_sidewalk_keeps_its_violation_no_trimmed(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [PROPERTY_ROW]]) as run:
            assert admin_client.patch(URL, json={"violation_no": " V-123 "}).status_code == 200
        assert run.call_args.args[1][5] == "V-123"


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
        assert data["display_address"] == "12-34 56th Ave" and data["block"] == "1234"

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
        assert "status" not in data and "pir_status" not in data and "display_address" in data

    def test_reading_one_carries_its_latest_task_order(self, admin_client):
        task_order_id = UUID("d0000000-0000-4000-8000-000000000001")
        row = {**PROPERTY_ROW, "task_order_id": task_order_id, "task_order_no": "TO-001"}
        with patch(PROPS, return_value=[row]) as query:
            data = admin_client.get(URL).json()["data"]
        assert data["task_order_id"] == str(task_order_id) and data["task_order_no"] == "TO-001"
        assert "latest.task_order_id" in query.call_args.args[0]

    def test_reading_one_in_no_task_order_carries_nulls(self, admin_client):
        with patch(PROPS, return_value=[{**PROPERTY_ROW, "task_order_id": None, "task_order_no": None}]):
            data = admin_client.get(URL).json()["data"]
        assert data["task_order_id"] is None and data["task_order_no"] is None

    def test_the_other_responses_carry_no_task_order(self, admin_client):
        with patch(PROPS, return_value=[PROPERTY_ROW]):
            data = admin_client.patch(URL, json={"notes": "x"}).json()["data"]
        assert "task_order_id" not in data and "task_order_no" not in data


class TestBlockAndLot:
    @pytest.mark.parametrize("block,lot,stored", [
        ("00123", "0045", ("123", "45")), ("00000", "0000", ("0", "0")), (" 7 ", "1", ("7", "1")),
        ("99999", "9999", ("99999", "9999")), ("000123", "00045", ("123", "45")),
    ])
    def test_a_block_and_lot_are_stored_without_leading_zeros(self, admin_client, block, lot, stored):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            assert admin_client.post(LIST_URL, json={**BODY, "block": block, "lot": lot}).status_code == 201
        assert run.call_args.args[1][3:5] == stored

    @pytest.mark.parametrize("field,value,detail", [
        ("block", "12a", "block must be digits only"), ("block", "", "block must be digits only"),
        ("block", "-1", "block must be digits only"), ("block", "1.5", "block must be digits only"),
        ("block", "123456", "block can have at most 5 digits"), ("lot", "12345", "lot can have at most 4 digits"),
        ("lot", "4 5", "lot must be digits only"),
    ])
    def test_a_block_or_lot_that_isnt_a_tax_map_number_is_422(self, admin_client, field, value, detail):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, field: value})
        assert response.status_code == 422 and detail in response.text
        run.assert_not_called()

    def test_the_borough_is_taken_as_sent(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            assert admin_client.post(LIST_URL, json={**BODY, "boro": "Bronx"}).status_code == 201
        assert run.call_args.args[1][2] == "Bronx"

    def test_an_edit_can_move_a_sidewalk_to_another_borough(self, admin_client):
        with patch(PROPS, side_effect=[[PROPERTY_ROW], [{**PROPERTY_ROW, "boro": "Brooklyn"}]]) as run:
            response = admin_client.patch(URL, json={"boro": "Brooklyn", "block": "0099"})
        assert response.status_code == 200 and response.json()["data"]["boro"] == "Brooklyn"
        params = run.call_args.args[1]
        assert params[:3] == ("Brooklyn", "99", "56") and params[-3:] == ("Brooklyn", "99", "56")

    def test_a_ped_ramp_keeps_its_borough_editable(self, admin_client):
        with patch(PROPS, side_effect=[[RAMP_ROW], [{**RAMP_ROW, "boro": "Queens"}]]) as run:
            assert admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"boro": "Queens"}).status_code == 200
        assert run.call_args.args[1][0] == "Queens"

    def test_undelete_checks_the_lot_among_live_sidewalks(self):
        with patch(PROPS, return_value=[]) as run:
            undelete_property(PROPERTY_ID)
        one_line = " ".join(run.call_args.args[0].split())
        assert ("other.kind = 'sidewalk' AND other.boro = p.boro AND other.block = p.block AND other.lot = p.lot"
                in one_line)

    def test_a_ped_ramp_restored_meanwhile_is_404_not_a_lot_clash(self, admin_client):
        deleted_ramp = {**RAMP_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID}
        with patch(PROPS, side_effect=[[deleted_ramp], []]) as run:
            response = admin_client.post(f"/v1/properties/{RAMP_ID}/admin/undelete")
        assert response.status_code == 404 and response.json() == {"detail": "Property is not deleted"}
        assert run.call_count == 2  # read, the statement; no lot lookup for a ped ramp


CORNERS = "api.queries.ped_ramp_corners.run_query"
CORNER_ID = UUID("3e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b")
CORNER_ELSEWHERE = {"detail": "Corner must be a live ped ramp corner of this project"}


class TestRampCorner:
    def test_a_ped_ramp_is_created_at_a_live_corner_of_its_project(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
             patch(CORNERS, return_value=[{"corner_id": CORNER_ID}]) as corner, \
             patch(PROPS, return_value=[{**RAMP_ROW, "corner_id": CORNER_ID}]) as run:
            response = admin_client.post(LIST_URL, json={**RAMP_BODY, "corner_id": str(CORNER_ID)})
        assert response.status_code == 201 and response.json()["data"]["corner_id"] == str(CORNER_ID)
        sql, params = corner.call_args.args
        assert "deleted_at IS NULL" in sql and params == (CORNER_ID, "HWS0023")
        assert run.call_args.args[1][-4] == CORNER_ID

    def test_a_corner_that_isnt_a_live_one_of_the_project_is_400(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CORNERS, return_value=[]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**RAMP_BODY, "corner_id": str(CORNER_ID)})
        assert (response.status_code, response.json()) == (400, CORNER_ELSEWHERE)
        run.assert_not_called()

    def test_a_sidewalk_takes_no_corner(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(PROPS) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "corner_id": str(CORNER_ID)})
        assert response.status_code == 422 and "corner_id" in str(response.json())
        run.assert_not_called()

    def test_an_edit_moves_a_ramp_to_another_corner_after_checking_it(self, admin_client):
        with patch(PROPS, side_effect=[[RAMP_ROW], [{**RAMP_ROW, "corner_id": CORNER_ID}]]) as run, \
             patch(CORNERS, return_value=[{"corner_id": CORNER_ID}]) as corner:
            response = admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"corner_id": str(CORNER_ID)})
        assert response.status_code == 200 and corner.call_count == 1
        assert CORNER_ID in run.call_args.args[1]

    def test_a_null_takes_a_ramp_off_its_corner_without_a_check(self, admin_client):
        with patch(PROPS, side_effect=[[{**RAMP_ROW, "corner_id": CORNER_ID}], [RAMP_ROW]]) as run, \
             patch(CORNERS) as corner:
            response = admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"corner_id": None})
        assert response.status_code == 200 and response.json()["data"]["corner_id"] is None
        corner.assert_not_called()
        assert CORNER_ID not in run.call_args.args[1]

    def test_an_edit_to_a_corner_of_another_project_is_400(self, admin_client):
        with patch(PROPS, return_value=[RAMP_ROW]) as run, patch(CORNERS, return_value=[]):
            response = admin_client.patch(f"/v1/properties/{RAMP_ID}", json={"corner_id": str(CORNER_ID)})
        assert (response.status_code, response.json()) == (400, CORNER_ELSEWHERE)
        assert run.call_count == 1


# ---------------------------------------------------------------------------
# Slice Y1: a sidewalk's tax-map frontage from MapPLUTO, fetched after a create or a move, and an admin's refresh
# ---------------------------------------------------------------------------

GET = "api.services.mappluto.httpx.get"
FETCHED = datetime(2026, 11, 5, 14, 0, tzinfo=timezone.utc)
CACHED_ROW = {**PROPERTY_ROW, "tax_map_frontage_ft": Decimal("40.00"), "tax_map_source": "mappluto_26v2",
              "tax_map_fetched_at": FETCHED}
REFRESHED = {"tax_map_frontage_ft": Decimal("42.50"), "tax_map_source": "mappluto_26v2", "tax_map_fetched_at": FETCHED,
             "old_frontage_ft": Decimal("40.00"), "pages_updated": 2}


def mappluto(front: object, version: str = "26v2") -> MagicMock:
    """
    Build MapPLUTO's answer for one lot.
    Takes its LotFront and Version.
    Returns the mock response.
    """
    response = MagicMock()
    response.json.return_value = {"features": [{"attributes": {"LotFront": front, "Version": version}}]}
    return response


class TestTaxMapFrontageFetched:
    def test_a_new_sidewalk_comes_back_without_one_and_is_fetched_after(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(GET, return_value=mappluto(40)) as get, \
                patch(PROPS, side_effect=[[PROPERTY_ROW], [{"id": PROPERTY_ID}]]) as run:
            response = admin_client.post(LIST_URL, json=BODY)
        data = response.json()["data"]
        assert response.status_code == 201
        assert (data["tax_map_frontage_ft"], data["tax_map_source"], data["tax_map_fetched_at"]) == (None, None, None)
        assert get.call_args.kwargs["params"]["where"] == "BBL=4012340056"
        sql, params = run.call_args.args
        assert "SET tax_map_frontage_ft = %s, tax_map_source = %s, tax_map_fetched_at = now()" in sql
        assert params == (Decimal("40.00"), "mappluto_26v2", PROPERTY_ID, "Queens", "1234", "56")

    def test_a_fetch_that_fails_leaves_the_sidewalk_created_without_one(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(GET, side_effect=httpx.ConnectError("down")), \
                patch(PROPS, return_value=[PROPERTY_ROW]) as run:
            response = admin_client.post(LIST_URL, json=BODY)
        assert response.status_code == 201 and run.call_count == 1

    def test_a_ped_ramp_isnt_looked_up(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(GET) as get, patch(PROPS, return_value=[RAMP_ROW]):
            response = admin_client.post(LIST_URL, json=RAMP_BODY)
        assert response.status_code == 201
        get.assert_not_called()

    def test_responses_carry_the_cached_frontage(self, admin_client):
        with patch(PROPS, return_value=[CACHED_ROW]):
            data = admin_client.get(URL).json()["data"]
        assert (data["tax_map_frontage_ft"], data["tax_map_source"]) == (40.0, "mappluto_26v2")
        assert data["tax_map_fetched_at"].startswith("2026-11-05T14:00:00")

    def test_a_move_to_another_lot_clears_it_and_fetches_the_new_lots(self, admin_client):
        moved = {**PROPERTY_ROW, "lot": "57"}
        with patch(GET, return_value=mappluto(25)) as get, \
                patch(PROPS, side_effect=[[CACHED_ROW], [moved], [{"id": PROPERTY_ID}]]) as run:
            response = admin_client.patch(URL, json={"lot": "57"})
        assert response.status_code == 200
        update_sql, update_params = run.call_args_list[1].args
        one_line = " ".join(update_sql.split())
        assert ("tax_map_frontage_ft = CASE WHEN (p.boro = new_lot.boro AND p.block IS NOT DISTINCT FROM new_lot.block "
                "AND p.lot IS NOT DISTINCT FROM new_lot.lot) THEN p.tax_map_frontage_ft END") in one_line
        assert "FROM (SELECT %s::icid.boro AS boro, %s::text AS block, %s::text AS lot) AS new_lot" in one_line
        assert update_params[-7:] == ("Queens", "1234", "57", PROPERTY_ID, "Queens", "1234", "57")
        assert get.call_args.kwargs["params"]["where"] == "BBL=4012340057"
        assert run.call_args.args[1] == (Decimal("25.00"), "mappluto_26v2", PROPERTY_ID, "Queens", "1234", "57")

    def test_an_edit_that_keeps_the_lot_fetches_nothing(self, admin_client):
        with patch(GET) as get, patch(PROPS, return_value=[CACHED_ROW]):
            response = admin_client.patch(URL, json={"notes": "corner lot"})
        assert response.status_code == 200
        get.assert_not_called()


class TestRefreshTaxMap:
    REFRESH = f"{URL}/admin/refresh-tax-map"

    def test_an_admin_refreshes_the_property_and_its_title_blocks(self, admin_client, caplog):
        with patch(GET, return_value=mappluto(42.5)), patch(PROPS, side_effect=[[CACHED_ROW], [REFRESHED]]) as run, \
                caplog.at_level("INFO", logger="api.v1.properties"):
            response = admin_client.post(self.REFRESH)
        assert response.status_code == 200
        assert response.json()["data"] == {"frontage_ft": 42.5, "source": "mappluto_26v2",
                                           "fetched_at": "2026-11-05T14:00:00Z", "pages_updated": 2}
        sql, params = run.call_args.args
        one_line = " ".join(sql.split())
        assert params == (Decimal("42.50"), "mappluto_26v2", PROPERTY_ID, "Queens", "1234", "56")
        assert "JOIN icid.pirs pir ON pir.property_id = prop.id AND pir.deleted_at IS NULL" in one_line
        assert "WHERE a.tax_map_source IS DISTINCT FROM 'manual'" in one_line
        assert "WHERE pages.page_number = 1 OR EXISTS" in one_line
        assert any("MapPLUTO refresh: property_id=" in record.message and "old_frontage=40.00" in record.message
                   and "new_frontage=42.50" in record.message and "pages_updated=2" in record.message
                   for record in caplog.records)

    def test_mappluto_down_is_503_and_writes_nothing(self, admin_client):
        with patch(GET, side_effect=httpx.ReadTimeout("slow")), patch(PROPS, return_value=[CACHED_ROW]) as run:
            response = admin_client.post(self.REFRESH)
        assert response.status_code == 503 and "nothing was changed" in response.json()["detail"]
        assert run.call_count == 1

    def test_no_usable_frontage_is_422_and_writes_nothing(self, admin_client):
        with patch(GET, return_value=mappluto(0)), patch(PROPS, return_value=[CACHED_ROW]) as run:
            response = admin_client.post(self.REFRESH)
        assert (response.status_code, response.json()["detail"]) == (
            422, "MapPLUTO has no usable frontage for Queens Block 1234 Lot 56")
        assert run.call_count == 1

    def test_a_move_meanwhile_is_409(self, admin_client):
        with patch(GET, return_value=mappluto(42.5)), patch(PROPS, side_effect=[[CACHED_ROW], []]):
            assert admin_client.post(self.REFRESH).status_code == 409

    def test_a_ped_ramp_is_400(self, admin_client):
        with patch(GET) as get, patch(PROPS, return_value=[RAMP_ROW]):
            response = admin_client.post(f"/v1/properties/{RAMP_ID}/admin/refresh-tax-map")
        assert (response.status_code, response.json()["detail"]) == (400, "A ped ramp has no tax lot to look up")
        get.assert_not_called()

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(PROPS, return_value=[]):
            assert admin_client.post(self.REFRESH).status_code == 404

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROPS) as run, patch(GET) as get:
            response = inspector.post(self.REFRESH)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()
        get.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(PROPS) as run:
            assert demo_client.post(self.REFRESH).status_code == 403
        run.assert_not_called()
