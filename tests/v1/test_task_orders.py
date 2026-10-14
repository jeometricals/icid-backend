from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.properties import soft_delete_property, undelete_property
from api.queries.task_orders import get_task_order, list_task_orders, soft_delete_task_order, undelete_task_order
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/projects/{project_id}/task-orders and /v1/task-orders/{task_order_id}, with its bundle
# Mock rows are dicts, as run_query returns them. A task order's properties come back as jsonb, so as plain
# JSON values (text for uuids and times).
# ---------------------------------------------------------------------------

TASKS = "api.queries.task_orders.run_query"
PROPS = "api.queries.properties.run_query"
PROJECTS = "api.queries.projects.run_query"
LIST_URL = "/v1/projects/HWS0023/task-orders"

ADMIN_ID = ADMIN_USER_ROW["uuid"]
TASK_ORDER_ID = UUID("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b")
OTHER_TASK_ORDER_ID = UUID("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8c")
PROPERTY_ID = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")
URL = f"/v1/task-orders/{TASK_ORDER_ID}"
BUNDLE_URL = f"{URL}/properties/{PROPERTY_ID}"
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DELETED = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)

PROJECT_ROW = {"project_id": "HWS0023", "project_name": "S/W Queens 2025", "project_description": None,
               "registration_code": None, "borough": "Queens", "status": None}
PROPERTY_ROW = {"id": PROPERTY_ID, "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": CREATED, "updated_at": CREATED,
                "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
# The same property as jsonb hands it back inside a task order
BUNDLED_JSON = {"id": str(PROPERTY_ID), "project_id": "HWS0023", "bbl": "4012340056", "address": "12-34 56th Ave",
                "property_type": "1_3_family", "notes": None, "created_at": "2026-10-14T09:00:00+00:00",
                "updated_at": "2026-10-14T09:00:00+00:00", "created_by": str(ADMIN_ID), "deleted_at": None,
                "deleted_by": None}
TASK_ORDER_ROW = {"id": TASK_ORDER_ID, "project_id": "HWS0023", "task_order_number": "TO-001",
                  "issued_date": date(2026, 10, 1), "estimate_notes": None, "created_at": CREATED,
                  "updated_at": CREATED, "created_by": ADMIN_ID, "deleted_at": None, "deleted_by": None}
DETAIL_ROW = {**TASK_ORDER_ROW, "properties": []}
BUNDLED_ROW = {**TASK_ORDER_ROW, "properties": [BUNDLED_JSON]}
DELETED_ROW = {**BUNDLED_ROW, "deleted_at": DELETED, "deleted_by": ADMIN_ID, "updated_at": DELETED}
OTHER_ROW = {**TASK_ORDER_ROW, "id": OTHER_TASK_ORDER_ID, "task_order_number": "TO-002"}
JOIN_ROW = {"task_order_id": TASK_ORDER_ID, "property_id": PROPERTY_ID, "added_at": CREATED, "added_by": ADMIN_ID}
BODY = {"task_order_number": "TO-001", "issued_date": "2026-10-01"}

INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})
ON_PROJECT = [[PROJECT_ROW], [{"?column?": 1}]]  # get_project_by_id, then is_user_on_project
OFF_PROJECT = [[PROJECT_ROW], []]


def flat(sql: str) -> str:
    """
    Squeeze a statement's whitespace to single spaces.
    Takes the SQL.
    Returns it on one line.
    """
    return " ".join(sql.split())


class TestCreateTaskOrder:
    def test_an_admin_creates_a_task_order_as_its_creator(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(TASKS, return_value=[DETAIL_ROW]) as run:
            response = admin_client.post(LIST_URL, json={**BODY, "task_order_number": " TO-001 ", "estimate_notes": ""})
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["task_order_number"] == "TO-001" and data["created_by"] == str(ADMIN_ID)
        assert data["issued_date"] == "2026-10-01" and data["properties"] == []
        sql, params = run.call_args.args
        assert "INSERT INTO icid.task_orders" in sql and "ON CONFLICT DO NOTHING" in sql
        assert params == ("HWS0023", "TO-001", date(2026, 10, 1), None, ADMIN_ID)

    def test_a_duplicate_number_in_the_project_is_409(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(TASKS, side_effect=[[], [OTHER_ROW]]) as run:
            response = admin_client.post(LIST_URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": "A task order with this number already exists in this project",
                                   "existing_task_order_id": str(OTHER_TASK_ORDER_ID)}
        assert run.call_args.args[1] == ("HWS0023", "TO-001")

    def test_the_same_number_in_another_project_is_created(self, admin_client):
        elsewhere = {**DETAIL_ROW, "project_id": "HWK2026"}
        with patch(PROJECTS, return_value=[{**PROJECT_ROW, "project_id": "HWK2026"}]), \
                patch(TASKS, return_value=[elsewhere]) as run:
            response = admin_client.post("/v1/projects/HWK2026/task-orders", json=BODY)
        assert response.status_code == 201 and response.json()["data"]["project_id"] == "HWK2026"
        assert run.call_args.args[1][:2] == ("HWK2026", "TO-001")

    def test_a_blank_number_is_400(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(TASKS) as run:
            response = admin_client.post(LIST_URL, json={"task_order_number": "  "})
        assert response.status_code == 400 and response.json() == {"detail": "task_order_number cannot be blank"}
        run.assert_not_called()

    def test_a_malformed_date_is_422(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(TASKS) as run:
            assert admin_client.post(LIST_URL, json={**BODY, "issued_date": "10/01/2026"}).status_code == 422
        run.assert_not_called()

    def test_an_unknown_project_is_404(self, admin_client):
        with patch(PROJECTS, return_value=[]), patch(TASKS) as run:
            assert admin_client.post("/v1/projects/NOPE/task-orders", json=BODY).status_code == 404
        run.assert_not_called()


class TestListTaskOrders:
    def test_each_carries_its_count_of_live_properties(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(TASKS, return_value=[{**TASK_ORDER_ROW, "property_count": 3}]) as run:
            response = admin_client.get(LIST_URL)
        assert response.status_code == 200
        assert response.json()["data"][0]["property_count"] == 3 and "properties" not in response.json()["data"][0]
        sql, params = run.call_args.args
        one_line = flat(sql)
        assert "FROM icid.task_order_properties tp JOIN icid.properties p ON p.id = tp.property_id" in one_line
        assert "WHERE tp.task_order_id = t.id AND p.deleted_at IS NULL) AS property_count" in one_line
        assert "(%s OR t.deleted_at IS NULL)" in sql and "ORDER BY t.task_order_number;" in sql
        assert params == ("HWS0023", False)

    def test_an_admin_can_ask_for_the_deleted_ones_too(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(TASKS, return_value=[]) as run:
            admin_client.get(LIST_URL, params={"include_deleted": "true"})
        assert run.call_args.args[1] == ("HWS0023", True)

    def test_include_deleted_from_a_user_who_isnt_an_admin_is_ignored(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=ON_PROJECT), \
                patch(TASKS, return_value=[]) as run:
            assert inspector.get(LIST_URL, params={"include_deleted": "true"}).status_code == 200
        assert run.call_args.args[1] == ("HWS0023", False)

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=OFF_PROJECT), patch(TASKS) as run:
            assert inspector.get(LIST_URL).status_code == 403
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(TASKS, return_value=None):
            assert admin_client.get(LIST_URL).status_code == 500


class TestGetTaskOrder:
    def test_a_user_on_its_project_gets_it_with_its_properties(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(TASKS, return_value=[BUNDLED_ROW]) as run:
            response = inspector.get(URL)
        assert response.status_code == 200
        properties = response.json()["data"]["properties"]
        assert [p["id"] for p in properties] == [str(PROPERTY_ID)] and properties[0]["bbl"] == "4012340056"
        assert run.call_args.args[1] == (TASK_ORDER_ID,)

    def test_the_detail_leaves_soft_deleted_properties_out(self, admin_client):
        with patch(TASKS, return_value=[DETAIL_ROW]) as run:
            admin_client.get(URL)
        one_line = flat(run.call_args.args[0])
        assert "jsonb_agg(to_jsonb(p) ORDER BY p.address, p.bbl)" in one_line
        assert "WHERE tp.task_order_id = t.id AND p.deleted_at IS NULL), '[]'::jsonb) AS properties" in one_line

    def test_a_user_who_isnt_on_its_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(TASKS, return_value=[DETAIL_ROW]):
            response = inspector.get(URL)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}

    def test_a_demo_user_gets_404_for_another_projects(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(TASKS, return_value=[DETAIL_ROW]):
            response = demo_client.get(URL)
        assert response.status_code == 404 and response.json() == {"detail": "Task order not found"}

    def test_an_unknown_task_order_is_404(self, admin_client):
        with patch(TASKS, return_value=[]):
            assert admin_client.get(URL).status_code == 404


class TestUpdateTaskOrder:
    def test_an_admin_changes_some_fields(self, admin_client):
        changed = {**DETAIL_ROW, "task_order_number": "TO-001A"}
        with patch(TASKS, return_value=[changed]) as run:
            response = admin_client.patch(URL, json={"task_order_number": " TO-001A "})
        assert response.status_code == 200 and response.json()["data"]["task_order_number"] == "TO-001A"
        sql, params = run.call_args.args
        assert "UPDATE icid.task_orders t" in sql and "updated_at = now()" in sql
        assert "other.deleted_at IS NULL" in sql and "t.deleted_at IS NOT NULL" in sql
        assert params == ("TO-001A", False, None, False, None, TASK_ORDER_ID, "TO-001A")

    def test_null_date_and_notes_clear_them(self, admin_client):
        with patch(TASKS, return_value=[DETAIL_ROW]) as run:
            assert admin_client.patch(URL, json={"issued_date": None, "estimate_notes": None}).status_code == 200
        assert run.call_args.args[1] == (None, True, None, True, None, TASK_ORDER_ID, None)

    def test_a_null_number_alone_is_nothing_to_change(self, admin_client):
        with patch(TASKS, return_value=[DETAIL_ROW]) as run:
            response = admin_client.patch(URL, json={"task_order_number": None})
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        assert run.call_count == 1  # the task order read, nothing written

    def test_a_number_another_task_order_of_the_project_has_is_409(self, admin_client):
        with patch(TASKS, side_effect=[[DETAIL_ROW], [], [DETAIL_ROW], [OTHER_ROW]]) as run:
            response = admin_client.patch(URL, json={"task_order_number": "TO-002"})
        assert response.status_code == 409
        assert response.json()["existing_task_order_id"] == str(OTHER_TASK_ORDER_ID)
        assert run.call_args.args[1] == ("HWS0023", "TO-002")

    def test_an_unknown_task_order_is_404(self, admin_client):
        with patch(TASKS, side_effect=[[], []]):
            assert admin_client.patch(URL, json={"estimate_notes": "x"}).status_code == 404

    def test_an_admin_edits_a_deleted_one(self, admin_client):
        with patch(TASKS, return_value=[DELETED_ROW]):
            assert admin_client.patch(URL, json={"estimate_notes": "x"}).status_code == 200


class TestSoftDeleteAndUndeleteTaskOrder:
    def test_the_round_trip_keeps_the_bundle(self, admin_client):
        with patch(TASKS, side_effect=[[BUNDLED_ROW], [DELETED_ROW]]):
            deleted = admin_client.post(f"{URL}/admin/soft-delete")
        assert deleted.status_code == 200 and deleted.json()["message"] == "Task order deleted"
        assert deleted.json()["data"]["deleted_at"] is not None
        assert deleted.json()["data"]["deleted_by"] == str(ADMIN_ID)
        assert len(deleted.json()["data"]["properties"]) == 1
        with patch(TASKS, side_effect=[[DELETED_ROW], [BUNDLED_ROW]]):
            restored = admin_client.post(f"{URL}/admin/undelete")
        assert restored.status_code == 200 and restored.json()["message"] == "Task order restored"
        assert restored.json()["data"]["deleted_at"] is None and restored.json()["data"]["deleted_by"] is None
        assert [p["id"] for p in restored.json()["data"]["properties"]] == [str(PROPERTY_ID)]

    def test_neither_statement_touches_the_bundle(self):
        with patch(TASKS, return_value=[]) as run:
            soft_delete_task_order(TASK_ORDER_ID, ADMIN_ID)
            undelete_task_order(TASK_ORDER_ID)
        for call in run.call_args_list:
            sql = call.args[0]
            assert sql.lstrip().startswith("UPDATE icid.task_orders t")
            assert "DELETE" not in sql and "INSERT" not in sql

    def test_deleting_it_again_changes_nothing(self, admin_client):
        with patch(TASKS, return_value=[DELETED_ROW]) as run:
            response = admin_client.post(f"{URL}/admin/soft-delete")
        assert response.status_code == 200 and response.json()["message"] == "Task order was already deleted"
        assert run.call_count == 1

    def test_undeleting_one_that_isnt_deleted_is_404(self, admin_client):
        with patch(TASKS, return_value=[DETAIL_ROW]):
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 404 and response.json() == {"detail": "Task order is not deleted"}

    def test_its_number_taken_meanwhile_is_409(self, admin_client):
        taker = {**OTHER_ROW, "task_order_number": "TO-001"}
        with patch(TASKS, side_effect=[[DELETED_ROW], [], [taker]]):
            response = admin_client.post(f"{URL}/admin/undelete")
        assert response.status_code == 409
        assert response.json() == {"detail": "Another task order in this project now has this number",
                                   "existing_task_order_id": str(OTHER_TASK_ORDER_ID)}

    @pytest.mark.parametrize("action", ["soft-delete", "undelete"])
    def test_a_project_member_who_isnt_an_admin_is_forbidden(self, action):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT[1]), \
                patch(TASKS) as run:
            response = inspector.post(f"{URL}/admin/{action}")
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()


class TestBundle:
    def test_an_admin_bundles_a_property_of_the_same_project(self, admin_client):
        with patch(TASKS, side_effect=[[DETAIL_ROW], [JOIN_ROW], [BUNDLED_ROW]]) as run, \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = admin_client.post(BUNDLE_URL)
        assert response.status_code == 201 and response.json()["message"] == "Property bundled"
        assert [p["id"] for p in response.json()["data"]["properties"]] == [str(PROPERTY_ID)]
        sql, params = run.call_args_list[1].args
        one_line = flat(sql)
        assert "INSERT INTO icid.task_order_properties (task_order_id, property_id, added_by)" in one_line
        assert "JOIN icid.properties p ON p.project_id = t.project_id" in one_line
        assert "t.deleted_at IS NULL AND p.deleted_at IS NULL" in one_line
        assert params == (ADMIN_ID, TASK_ORDER_ID, PROPERTY_ID)

    def test_a_property_already_in_it_is_409(self, admin_client):
        with patch(TASKS, side_effect=[[DETAIL_ROW], [], [{"?column?": 1}]]), \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = admin_client.post(BUNDLE_URL)
        assert response.status_code == 409 and response.json() == {"detail": "Property is already in this task order"}

    @pytest.mark.parametrize("task_order,found,detail", [
        (DETAIL_ROW, {**PROPERTY_ROW, "project_id": "HWK2026"}, "Property belongs to a different project"),
        (DELETED_ROW, PROPERTY_ROW, "Cannot bundle into a deleted task order"),
        (DETAIL_ROW, {**PROPERTY_ROW, "deleted_at": DELETED}, "Cannot bundle a deleted property"),
    ])
    def test_what_cant_be_bundled_is_400(self, admin_client, task_order, found, detail):
        with patch(TASKS, return_value=[task_order]) as run, patch(PROPS, return_value=[found]):
            response = admin_client.post(BUNDLE_URL)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        assert run.call_count == 1  # nothing written

    def test_an_unknown_property_is_404(self, admin_client):
        with patch(TASKS, return_value=[DETAIL_ROW]), patch(PROPS, return_value=[]):
            response = admin_client.post(BUNDLE_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Property not found"}

    def test_an_unknown_task_order_is_404(self, admin_client):
        with patch(TASKS, return_value=[]), patch(PROPS) as props:
            response = admin_client.post(BUNDLE_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Task order not found"}
        props.assert_not_called()


class TestUnbundle:
    def test_an_admin_unbundles_a_property(self, admin_client):
        with patch(TASKS, side_effect=[[DETAIL_ROW], [{"task_order_id": TASK_ORDER_ID}]]) as run:
            response = admin_client.delete(BUNDLE_URL)
        assert response.status_code == 204 and response.content == b""
        sql, params = run.call_args.args
        assert "DELETE FROM icid.task_order_properties" in sql and params == (TASK_ORDER_ID, PROPERTY_ID)

    def test_it_works_on_a_deleted_task_order(self, admin_client):
        with patch(TASKS, side_effect=[[DELETED_ROW], [{"task_order_id": TASK_ORDER_ID}]]):
            assert admin_client.delete(BUNDLE_URL).status_code == 204

    def test_a_property_not_in_it_is_404(self, admin_client):
        with patch(TASKS, side_effect=[[DETAIL_ROW], []]):
            response = admin_client.delete(BUNDLE_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Property is not in this task order"}


class TestProjectMembersWrite:
    """Create, edit, bundle and unbundle are open to anyone on the project; demo users and outsiders stay out."""

    def test_a_member_creates_a_task_order_as_its_creator(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=ON_PROJECT), \
                patch(TASKS, return_value=[{**DETAIL_ROW, "created_by": INSPECTOR_ROW["uuid"]}]) as run:
            response = inspector.post(LIST_URL, json=BODY)
        assert response.status_code == 201
        assert response.json()["data"]["created_by"] == str(INSPECTOR_ROW["uuid"])
        assert run.call_args.args[1][-1] == INSPECTOR_ROW["uuid"]

    def test_a_member_edits_a_task_order(self):
        changed = {**DETAIL_ROW, "estimate_notes": "two crews"}
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT[1]), \
                patch(TASKS, return_value=[changed]):
            response = inspector.patch(URL, json={"estimate_notes": "two crews"})
        assert response.status_code == 200 and response.json()["data"]["estimate_notes"] == "two crews"

    def test_a_member_bundles_a_property_as_its_adder(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT[1]), \
                patch(TASKS, side_effect=[[DETAIL_ROW], [JOIN_ROW], [BUNDLED_ROW]]) as run, \
                patch(PROPS, return_value=[PROPERTY_ROW]):
            response = inspector.post(BUNDLE_URL)
        assert response.status_code == 201
        assert run.call_args_list[1].args[1] == (INSPECTOR_ROW["uuid"], TASK_ORDER_ID, PROPERTY_ID)

    def test_a_member_unbundles_a_property(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT[1]), \
                patch(TASKS, side_effect=[[DETAIL_ROW], [{"task_order_id": TASK_ORDER_ID}]]):
            assert inspector.delete(BUNDLE_URL).status_code == 204

    @pytest.mark.parametrize("method,url,body", [("patch", URL, {"estimate_notes": "x"}), ("delete", BUNDLE_URL, None)])
    def test_a_member_cant_change_a_deleted_task_order(self, method, url, body):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT[1]), \
                patch(TASKS, return_value=[DELETED_ROW]) as run:
            response = inspector.request(method, url, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "Task order is deleted"}
        assert run.call_count == 1  # read, nothing written

    def test_a_member_cant_bundle_into_a_deleted_task_order(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=ON_PROJECT[1]), \
                patch(TASKS, return_value=[DELETED_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]):
            response = inspector.post(BUNDLE_URL)
        assert response.status_code == 400 and response.json() == {"detail": "Cannot bundle into a deleted task order"}

    def test_a_user_who_isnt_on_the_project_cant_create(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=OFF_PROJECT), patch(TASKS) as run:
            response = inspector.post(LIST_URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        run.assert_not_called()

    @pytest.mark.parametrize("method,url,body", [
        ("patch", URL, {"estimate_notes": "x"}), ("post", BUNDLE_URL, None), ("delete", BUNDLE_URL, None),
    ])
    def test_a_user_who_isnt_on_the_project_cant_change_its_task_orders(self, method, url, body):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(TASKS, return_value=[DETAIL_ROW]) as run, patch(PROPS) as props:
            response = inspector.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Project access required"}
        assert run.call_count == 1  # the task order read for the check
        props.assert_not_called()

    def test_a_demo_user_cant_create_on_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=ON_PROJECT[1]), patch(TASKS) as run:
            response = demo_client.post("/v1/projects/DEMO01/task-orders", json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: task orders are read-only"}
        run.assert_not_called()

    @pytest.mark.parametrize("method,url,body", [
        ("patch", URL, {"estimate_notes": "x"}), ("post", BUNDLE_URL, None), ("delete", BUNDLE_URL, None),
    ])
    def test_a_demo_user_cant_change_their_projects_task_orders(self, demo_client, method, url, body):
        with patch(PROJECTS, return_value=ON_PROJECT[1]), patch(TASKS, return_value=[DETAIL_ROW]) as run, \
                patch(PROPS) as props:
            response = demo_client.request(method, url, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: task orders are read-only"}
        assert run.call_count == 1
        props.assert_not_called()

    def test_a_demo_user_gets_404_creating_on_another_project(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(TASKS) as run:
            assert demo_client.post(LIST_URL, json=BODY).status_code == 404
        run.assert_not_called()

    @pytest.mark.parametrize("method,url,body", [
        ("patch", URL, {"estimate_notes": "x"}), ("post", BUNDLE_URL, None), ("delete", BUNDLE_URL, None),
    ])
    def test_a_demo_user_gets_404_for_another_projects_task_order(self, demo_client, method, url, body):
        with patch(PROJECTS, return_value=[]), patch(TASKS, return_value=[DETAIL_ROW]):
            response = demo_client.request(method, url, json=body)
        assert response.status_code == 404 and response.json() == {"detail": "Task order not found"}


class TestSoftDeletedPropertiesStayBundled:
    def test_a_property_soft_delete_and_undelete_leave_the_bundle_alone(self):
        with patch(PROPS, return_value=[]) as run:
            soft_delete_property(PROPERTY_ID, ADMIN_ID)
            undelete_property(PROPERTY_ID)
        for call in run.call_args_list:
            assert "task_order_properties" not in call.args[0]

    def test_the_task_order_hides_it_while_deleted_and_shows_it_once_back(self, admin_client):
        # The bundle row stays throughout; what the detail statement returns follows the property's deleted_at
        with patch(TASKS, return_value=[DETAIL_ROW]):
            hidden = admin_client.get(URL)
        with patch(TASKS, return_value=[BUNDLED_ROW]):
            shown = admin_client.get(URL)
        assert hidden.json()["data"]["properties"] == []
        assert [p["id"] for p in shown.json()["data"]["properties"]] == [str(PROPERTY_ID)]

    def test_both_readers_filter_on_the_propertys_deleted_at(self):
        with patch(TASKS, return_value=[]) as run:
            get_task_order(TASK_ORDER_ID)
            list_task_orders("HWS0023")
        for call in run.call_args_list:
            assert "p.deleted_at IS NULL" in call.args[0]

    def test_the_short_read_has_no_properties(self):
        with patch(TASKS, return_value=[TASK_ORDER_ROW]) as run:
            assert get_task_order(TASK_ORDER_ID, include_properties=False) == TASK_ORDER_ROW
        assert "task_order_properties" not in run.call_args.args[0]
