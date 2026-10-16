from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.non_assessable_areas import _WRITABLE, list_areas_for_flag, soft_delete_area
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/flags/{flag_id}/non-assessable-areas and /v1/non-assessable-areas/{non_assessable_area_id}
# Mock rows are dicts, as run_query returns them: an area's own columns, and its flag's page, PIR, property, project
# and deleted states.
# ---------------------------------------------------------------------------

AREAS = "api.queries.non_assessable_areas.run_query"
FLAGS = "api.queries.flags.run_query"
PROJECTS = "api.queries.projects.run_query"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
MEMBER_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
MEMBER_ID = MEMBER_ROW["uuid"]
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
PIR_ID = UUID("6f7a8b9c-0d1e-4f2a-8b3c-4d5e6f7a8b9c")
FLAG_ID = UUID("7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d")
PAGE_ID = UUID("8b9c0d1e-2f3a-4b4c-8d5e-6f7a8b9c0d1e")
AREA_ID = UUID("9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e2f")
FLAG_AREAS_URL = f"/v1/flags/{FLAG_ID}/non-assessable-areas"
URL = f"/v1/non-assessable-areas/{AREA_ID}"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)

FLAG_ROW = {"id": FLAG_ID, "pir_id": PIR_ID, "pir_page_id": PAGE_ID, "property_id": PROPERTY_ID, "project_id": "HWS0023",
            "display_order": 1, "added_in_field": False, "shape": "rectangle", "dimensions": {"L": 5.0, "W": 4.0},
            "sketch_x": None, "sketch_y": None, "rotation": 0.0, "surveyed_mark": "cross",
            "surveyed_is_assessable": True, "surveyed_non_assessable_reason": None, "surveyed_thickness": "4in",
            "is_constructed": False, "pay_item_id": None, "defect_codes": [], "computed_quantity": Decimal("20.00"),
            "reported_quantity": None, "non_assessable_areas_count": 0, "non_assessable_quantity": Decimal("0"),
            "subtracted_quantity": Decimal("0"), "net_quantity": Decimal("20.00"), "quantity": Decimal("20.00"),
            "is_overridden": False, "quantity_delta_reason": None, "created_at": CREATED, "updated_at": CREATED,
            "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None, "page_deleted_at": None,
            "pir_deleted_at": None, "property_deleted_at": None}
AREA_ROW = {"non_assessable_area_id": AREA_ID, "flag_id": FLAG_ID, "display_order": 1, "shape": "rectangle",
            "dimensions": {"L": 2.0, "W": 1.5}, "sketch_x": 10.0, "sketch_y": 12.0, "rotation": 0.0,
            "reason": "driveway", "computed_quantity": Decimal("3.00"), "created_at": CREATED, "updated_at": CREATED,
            "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None, "pir_page_id": PAGE_ID, "pir_id": PIR_ID,
            "property_id": PROPERTY_ID, "project_id": "HWS0023", "flag_deleted_at": None, "page_deleted_at": None,
            "pir_deleted_at": None, "property_deleted_at": None}
DELETED_ROW = {**AREA_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID}
BODY = {"display_order": 1, "shape": "rectangle", "dimensions": {"L": 2, "W": 1.5}, "sketch_x": 10, "sketch_y": 12,
        "reason": "driveway"}

ON_PROJECT = [{"?column?": 1}]
PLACEMENT = "api.v1.non_assessable_areas.get_flag_placement"
# A 5 x 4 ft flag at 10 px/ft, its middle at (10, 12): it spans x -15..35, y -8..32 on its page
PLACED_FLAG = {"shape": "rectangle", "dimensions": {"L": 5.0, "W": 4.0}, "sketch_x": 10.0, "sketch_y": 12.0,
               "rotation": 0.0, "scale_px_per_ft": Decimal("10")}


@pytest.fixture(autouse=True)
def no_flag_place():
    """Answer the flag's place for the within-its-flag check with no flag, so it passes, unless a test sets it."""
    with patch(PLACEMENT, return_value=[]) as placement:
        yield placement
DEMO_READ_ONLY = {"detail": "Demo mode: flags are read-only"}

# Where a write's parameters sit: the writable columns, then the computed area, then (on create) the creator
AREA = len(_WRITABLE)
CREATOR = AREA + 1


def written(run, call: int = -1) -> dict:
    """
    Read the writable values a create or update statement was given.
    Takes the run_query mock and which of its calls (the last by default).
    Returns them keyed by column, with the computed area under computed_quantity.
    """
    params = run.call_args_list[call].args[1]
    values = dict(zip(_WRITABLE, params))
    values["computed_quantity"] = params[AREA]
    return values


def one_line(sql: str) -> str:
    """
    Collapse a statement's whitespace, to look for a clause in it.
    Takes the SQL.
    Returns it on one line.
    """
    return " ".join(sql.split())


class TestCreateArea:
    def test_an_admin_adds_an_area_with_its_computed_area(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=[AREA_ROW]) as run:
            response = admin_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 201 and response.json()["message"] == "Non-assessable area created"
        data = response.json()["data"]
        assert data["computed_quantity"] == 3.0 and data["flag_id"] == str(FLAG_ID) and data["project_id"] == "HWS0023"
        values = written(run)
        assert values["computed_quantity"] == Decimal("3.00") and values["reason"] == "driveway"
        assert run.call_args.args[1][CREATOR] == ADMIN_ID and run.call_args.args[1][CREATOR + 1] == FLAG_ID

    def test_a_member_adds_an_area_as_its_creator(self):
        with signed_in(MEMBER_ROW) as member, patch(FLAGS, return_value=[FLAG_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), \
                patch(AREAS, return_value=[{**AREA_ROW, "created_by": MEMBER_ID}]) as run:
            response = member.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["created_by"] == str(MEMBER_ID)
        assert run.call_args.args[1][CREATOR] == MEMBER_ID

    def test_the_statement_takes_only_a_live_flag_on_a_live_page_and_skips_a_taken_order(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=[AREA_ROW]) as run:
            admin_client.post(FLAG_AREAS_URL, json=BODY)
        sql = one_line(run.call_args.args[0])
        assert "INSERT INTO icid.non_assessable_areas" in sql
        assert "JOIN icid.pir_pages page ON page.pir_page_id = flag.pir_page_id AND page.deleted_at IS NULL" in sql
        assert "WHERE flag.id = %s AND flag.deleted_at IS NULL" in sql
        assert "ON CONFLICT DO NOTHING" in sql
        assert "%s::jsonb" in sql and "%s::real" in sql

    @pytest.mark.parametrize("changes", [
        {"dimensions": {"a": 1, "b": 2}},
        {"dimensions": {"L": 0, "W": 1}},
        {"display_order": 0},
        {"reason": "tree"},
        {"shape": "circle", "dimensions": {"r": 1}},
    ], ids=["wrong-keys", "zero-length", "order-0", "flag-reason", "circle"])
    def test_an_area_that_breaks_a_rule_is_422(self, admin_client, changes):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS) as run:
            response = admin_client.post(FLAG_AREAS_URL, json={**BODY, **changes})
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_taken_display_order_is_409(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]) as flags, patch(AREAS, return_value=[]):
            response = admin_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": "A non-assessable area of this flag already has display_order 1"}
        assert flags.call_count == 2  # read again to tell a taken order from a flag deleted meanwhile

    def test_a_flag_deleted_between_the_check_and_the_statement_is_400(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [{**FLAG_ROW, "deleted_at": DELETED}]]), patch(AREAS, return_value=[]):
            response = admin_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Flag is deleted"}

    def test_a_page_replaced_between_the_check_and_the_statement_is_400(self, admin_client):
        with patch(FLAGS, side_effect=[[FLAG_ROW], [{**FLAG_ROW, "page_deleted_at": DELETED}]]), \
                patch(AREAS, return_value=[]):
            response = admin_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Page is deleted"}

    @pytest.mark.parametrize("parent,detail", [("deleted_at", "Flag is deleted"), ("page_deleted_at", "Page is deleted")])
    def test_nobody_adds_to_a_deleted_flag_or_a_replaced_page(self, admin_client, parent, detail):
        with patch(FLAGS, return_value=[{**FLAG_ROW, parent: DELETED}]), patch(AREAS) as run:
            response = admin_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    @pytest.mark.parametrize("parent,detail", [("pir_deleted_at", "PIR is deleted"),
                                               ("property_deleted_at", "Property is deleted")])
    def test_a_member_cant_add_under_a_deleted_pir_or_property(self, parent, detail):
        with signed_in(MEMBER_ROW) as member, patch(FLAGS, return_value=[{**FLAG_ROW, parent: DELETED}]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(AREAS) as run:
            response = member.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_an_admin_adds_under_a_deleted_pir(self, admin_client):
        with patch(FLAGS, return_value=[{**FLAG_ROW, "pir_deleted_at": DELETED}]), patch(AREAS, return_value=[AREA_ROW]):
            assert admin_client.post(FLAG_AREAS_URL, json=BODY).status_code == 201

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as member, patch(FLAGS, return_value=[FLAG_ROW]), patch(PROJECTS, return_value=[]), \
                patch(AREAS) as run:
            response = member.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    def test_a_demo_user_cant_add_on_their_own_project(self, demo_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(PROJECTS, return_value=ON_PROJECT), patch(AREAS) as run:
            response = demo_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 403 and response.json() == DEMO_READ_ONLY
        run.assert_not_called()

    def test_a_demo_user_gets_404_on_another_project(self, demo_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(PROJECTS, return_value=[]), patch(AREAS) as run:
            response = demo_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Flag not found"}
        run.assert_not_called()

    def test_an_unknown_flag_is_404(self, admin_client):
        with patch(FLAGS, return_value=[]), patch(AREAS) as run:
            response = admin_client.post(FLAG_AREAS_URL, json=BODY)
        assert response.status_code == 404 and response.json() == {"detail": "Flag not found"}
        run.assert_not_called()


class TestWithinItsFlag:
    def test_an_area_inside_its_flag_is_created(self, admin_client, no_flag_place):
        no_flag_place.return_value = [PLACED_FLAG]
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=[AREA_ROW]):
            assert admin_client.post(FLAG_AREAS_URL, json=BODY).status_code == 201
        no_flag_place.assert_called_once_with(FLAG_ID)

    def test_an_area_reaching_past_its_flag_is_400_and_nothing_is_written(self, admin_client, no_flag_place):
        no_flag_place.return_value = [PLACED_FLAG]
        # 2 x 1.5 ft with its middle on the flag's right edge (x 35): half of it, 1.50 SF, outside
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=[AREA_ROW]) as run:
            response = admin_client.post(FLAG_AREAS_URL, json={**BODY, "sketch_x": 35})
        assert response.status_code == 400
        assert response.json() == {"detail": f"Non-assessable area 1 extends past flag {FLAG_ID}'s bounds by 1.50 SF; "
                                             "please redraw or split across adjacent flags."}
        run.assert_not_called()

    def test_an_edit_that_moves_it_past_its_flag_is_400(self, admin_client, no_flag_place):
        no_flag_place.return_value = [PLACED_FLAG]
        with patch(AREAS, return_value=[AREA_ROW]) as run:
            response = admin_client.patch(URL, json={"dimensions": {"L": 2, "W": 50}})
        assert response.status_code == 400 and "extends past flag" in response.json()["detail"]
        assert run.call_count == 1  # read, not written
        no_flag_place.assert_called_once_with(FLAG_ID)

    def test_without_a_scale_it_cant_be_told_and_is_allowed(self, admin_client, no_flag_place):
        no_flag_place.return_value = [{**PLACED_FLAG, "scale_px_per_ft": None}]
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=[AREA_ROW]):
            assert admin_client.post(FLAG_AREAS_URL, json={**BODY, "sketch_x": 900}).status_code == 201

    def test_a_flag_place_that_cant_be_read_is_500(self, admin_client, no_flag_place):
        no_flag_place.return_value = None
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=[AREA_ROW]):
            assert admin_client.post(FLAG_AREAS_URL, json=BODY).status_code == 500


class TestListAndReadAreas:
    def test_a_member_lists_a_flags_live_areas_by_display_order(self):
        second = {**AREA_ROW, "non_assessable_area_id": UUID("9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e30"), "display_order": 2}
        with signed_in(MEMBER_ROW) as member, patch(FLAGS, return_value=[FLAG_ROW]), \
                patch(PROJECTS, return_value=ON_PROJECT), patch(AREAS, return_value=[AREA_ROW, second]) as run:
            response = member.get(FLAG_AREAS_URL)
        assert response.status_code == 200 and [a["display_order"] for a in response.json()["data"]] == [1, 2]
        sql = one_line(run.call_args.args[0])
        assert "WHERE na.flag_id = %s AND na.deleted_at IS NULL ORDER BY na.display_order" in sql
        assert run.call_args.args[1] == (FLAG_ID,)

    def test_a_user_who_isnt_on_the_project_cant_list(self):
        with signed_in(MEMBER_ROW) as member, patch(FLAGS, return_value=[FLAG_ROW]), patch(PROJECTS, return_value=[]), \
                patch(AREAS) as run:
            assert member.get(FLAG_AREAS_URL).status_code == 403
        run.assert_not_called()

    def test_a_list_that_cant_be_read_is_500(self, admin_client):
        with patch(FLAGS, return_value=[FLAG_ROW]), patch(AREAS, return_value=None):
            assert admin_client.get(FLAG_AREAS_URL).status_code == 500

    def test_a_member_reads_one_area_deleted_or_not(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(AREAS, return_value=[DELETED_ROW]):
            response = member.get(URL)
        assert response.status_code == 200 and response.json()["data"]["deleted_at"] is not None

    def test_a_user_who_isnt_on_the_project_cant_read_one(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), patch(AREAS, return_value=[AREA_ROW]):
            assert member.get(URL).status_code == 403

    def test_a_demo_user_gets_404_for_another_projects_area(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(AREAS, return_value=[AREA_ROW]):
            response = demo_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Non-assessable area not found"}

    def test_an_unknown_area_is_404(self, admin_client):
        with patch(AREAS, return_value=[]):
            response = admin_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Non-assessable area not found"}


class TestUpdateArea:
    def test_a_new_shape_with_its_dimensions_is_recomputed(self, admin_client):
        with patch(AREAS, side_effect=[[AREA_ROW], [{**AREA_ROW, "shape": "triangle"}]]) as run:
            response = admin_client.patch(URL, json={"shape": "triangle", "dimensions": {"a": 3, "b": 4}})
        assert response.status_code == 200 and response.json()["message"] == "Non-assessable area updated"
        values = written(run)
        assert values["shape"] == "triangle" and values["computed_quantity"] == Decimal("6.00")
        assert values["reason"] == "driveway"  # the rest kept from the stored area

    def test_the_statement_refuses_a_display_order_another_live_area_has(self, admin_client):
        with patch(AREAS, side_effect=[[AREA_ROW], [AREA_ROW]]) as run:
            admin_client.patch(URL, json={"display_order": 2})
        sql = one_line(run.call_args.args[0])
        assert "AND NOT EXISTS ( SELECT 1 FROM icid.non_assessable_areas other" in sql
        assert "other.display_order = %s::int" in sql and run.call_args.args[1][-1] == 2

    def test_a_taken_display_order_is_409(self, admin_client):
        with patch(AREAS, side_effect=[[AREA_ROW], []]):
            response = admin_client.patch(URL, json={"display_order": 2})
        assert response.status_code == 409
        assert response.json() == {"detail": "A non-assessable area of this flag already has display_order 2"}

    def test_a_new_shape_without_dimensions_is_422(self, admin_client):
        with patch(AREAS, return_value=[AREA_ROW]) as run:
            response = admin_client.patch(URL, json={"shape": "triangle"})
        assert response.status_code == 422 and run.call_count == 1

    def test_the_merged_area_is_checked_against_the_rules(self, admin_client):
        with patch(AREAS, return_value=[AREA_ROW]) as run:
            response = admin_client.patch(URL, json={"dimensions": {"a": 1, "b": 2}})
        assert response.status_code == 422 and run.call_count == 1

    @pytest.mark.parametrize("body", [{}, {"reason": None, "display_order": None}])
    def test_nothing_to_change_is_400(self, admin_client, body):
        with patch(AREAS, return_value=[AREA_ROW]) as run:
            response = admin_client.patch(URL, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1

    @pytest.mark.parametrize("area,detail", [
        ({"deleted_at": DELETED}, "Non-assessable area is deleted"),
        ({"flag_deleted_at": DELETED}, "Flag is deleted"),
        ({"page_deleted_at": DELETED}, "Page is deleted"),
        ({"pir_deleted_at": DELETED}, "PIR is deleted"),
        ({"property_deleted_at": DELETED}, "Property is deleted"),
    ])
    def test_a_member_cant_edit_under_something_deleted(self, area, detail):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(AREAS, return_value=[{**AREA_ROW, **area}]) as run:
            response = member.patch(URL, json={"reason": "grass"})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1

    def test_an_admin_edits_a_deleted_area(self, admin_client):
        with patch(AREAS, side_effect=[[DELETED_ROW], [DELETED_ROW]]):
            assert admin_client.patch(URL, json={"reason": "grass"}).status_code == 200

    def test_a_member_edits_an_area(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(AREAS, side_effect=[[AREA_ROW], [{**AREA_ROW, "reason": "grass"}]]):
            response = member.patch(URL, json={"reason": "grass"})
        assert response.status_code == 200 and response.json()["data"]["reason"] == "grass"

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), \
                patch(AREAS, return_value=[AREA_ROW]) as run:
            assert member.patch(URL, json={"reason": "grass"}).status_code == 403
        assert run.call_count == 1

    def test_a_demo_user_cant_edit_on_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(AREAS, return_value=[AREA_ROW]) as run:
            response = demo_client.patch(URL, json={"reason": "grass"})
        assert response.status_code == 403 and response.json() == DEMO_READ_ONLY
        assert run.call_count == 1

    def test_an_unknown_area_is_404(self, admin_client):
        with patch(AREAS, return_value=[]):
            assert admin_client.patch(URL, json={"reason": "grass"}).status_code == 404


class TestDeleteArea:
    def test_a_member_soft_deletes_an_area(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(AREAS, side_effect=[[AREA_ROW], [DELETED_ROW]]) as run:
            response = member.delete(URL)
        assert response.status_code == 204
        sql, params = run.call_args.args
        assert "SET deleted_at = now(), deleted_by = %s" in sql and "deleted_at IS NULL" in sql
        assert params == (MEMBER_ID, AREA_ID)

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(AREAS, return_value=[DELETED_ROW]) as run:
            assert admin_client.delete(URL).status_code == 204
        assert run.call_count == 1

    @pytest.mark.parametrize("parent,detail", [
        ("flag_deleted_at", "Flag is deleted"), ("page_deleted_at", "Page is deleted"),
        ("pir_deleted_at", "PIR is deleted"), ("property_deleted_at", "Property is deleted"),
    ])
    def test_a_member_cant_delete_under_something_deleted(self, parent, detail):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=ON_PROJECT), \
                patch(AREAS, return_value=[{**AREA_ROW, parent: DELETED}]) as run:
            response = member.delete(URL)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1

    def test_an_admin_deletes_under_a_deleted_flag(self, admin_client):
        with patch(AREAS, side_effect=[[{**AREA_ROW, "flag_deleted_at": DELETED}], [DELETED_ROW]]) as run:
            assert admin_client.delete(URL).status_code == 204
        assert run.call_count == 2

    def test_a_demo_user_cant_delete(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT), patch(AREAS, return_value=[AREA_ROW]) as run:
            response = demo_client.delete(URL)
        assert response.status_code == 403 and response.json() == DEMO_READ_ONLY
        assert run.call_count == 1

    def test_a_user_who_isnt_on_the_project_cant_delete(self):
        with signed_in(MEMBER_ROW) as member, patch(PROJECTS, return_value=[]), patch(AREAS, return_value=[AREA_ROW]):
            assert member.delete(URL).status_code == 403

    def test_an_unknown_area_is_404(self, admin_client):
        with patch(AREAS, return_value=[]):
            assert admin_client.delete(URL).status_code == 404


def test_every_area_statement_is_in_the_icid_schema():
    with patch(AREAS, return_value=[]) as run:
        list_areas_for_flag(FLAG_ID)
        soft_delete_area(AREA_ID, ADMIN_ID)
    for call in run.call_args_list:
        sql = call.args[0]
        assert "icid.non_assessable_areas" in sql and "JOIN icid.flags f" in sql and "JOIN icid.properties p" in sql
