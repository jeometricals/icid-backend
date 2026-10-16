from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.contract_items import list_flag_pay_items
from api.queries.progress import list_flag_progress
from api.services.pay_item_units import FLAG_UNITS
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/progress-rollup and GET /v1/task-orders/{task_order_id}/progress-rollup
# Mock rows are dicts, as run_query returns them; a task order's properties come back as jsonb.
# ---------------------------------------------------------------------------

PROJECTS = "api.queries.projects.run_query"
CONTRACT_ITEMS = "api.queries.contract_items.run_query"
PROGRESS = "api.queries.progress.run_query"
TASKS = "api.queries.task_orders.run_query"
PROPS = "api.queries.properties.run_query"

PROJECT_ID = "SE384"
URL = f"/v1/projects/{PROJECT_ID}/progress-rollup"
TASK_ORDER_ID = UUID("5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b")
TO_URL = f"/v1/task-orders/{TASK_ORDER_ID}/progress-rollup"
MAIN_ST = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e51")
ELM_ST = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e52")
OAK_ST = UUID("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e53")
SIDEWALK = UUID("10000000-0000-4000-8000-000000000001")
CONCRETE = UUID("10000000-0000-4000-8000-000000000002")
CREATED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)

PROJECT_ROW = {"project_id": PROJECT_ID, "project_name": "Sidewalks Queens", "project_description": None,
               "registration_code": None, "borough": "Queens", "status": None}
INSPECTOR_ROW = {**ADMIN_USER_ROW, "uuid": UUID("b0000000-0000-4000-8000-000000000002"), "role": None}
ON_PROJECT = [[PROJECT_ROW], [{"?column?": 1}]]  # get_project_by_id, then is_user_on_project
OFF_PROJECT = [[PROJECT_ROW], []]

PAY_ITEMS = [
    {"pay_item_id": SIDEWALK, "item_no": "4.13 AAS", "budget_code": "SE384-CONS", "description": 'Sidewalk 4"',
     "unit": "SF", "unit_price": Decimal("11.28")},
    {"pay_item_id": CONCRETE, "item_no": "73.21AC", "budget_code": "SE384-CONS", "description": "Concrete base",
     "unit": "CY", "unit_price": Decimal("141.16")},
]
ADDRESSES = {MAIN_ST: "138-22 227 Street", ELM_ST: "350 Main St", OAK_ST: "7 Oak St"}


def place(property_id: UUID) -> dict:
    """
    Build a sidewalk's address fields, as list_flag_progress's rows carry them.
    Takes the property's uuid.
    Returns the fields.
    """
    return {"property_kind": "sidewalk", "address": ADDRESSES[property_id], "intersection_street_1": None,
            "intersection_street_2": None, "quadrant": None, "facing_street": None}


def flags(property_id: UUID, pay_item_id, state: str, flag_count: int, quantity: str) -> dict:
    """
    Build a row as list_flag_progress returns one.
    Takes the property, the pay item (None when unmapped), the state, the flag count and the summed quantity.
    Returns the row.
    """
    return {"property_id": property_id, **place(property_id), "pay_item_id": pay_item_id, "state": state,
            "flag_count": flag_count, "quantity": Decimal(quantity)}


def bundled(property_id: UUID) -> dict:
    """
    Build a bundled property as a task order's jsonb hands it back.
    Takes the property's uuid.
    Returns the dict.
    """
    return {"id": str(property_id), "project_id": PROJECT_ID, "kind": "sidewalk", "boro": "Queens", "block": "1",
            "lot": "1", "address": ADDRESSES[property_id], "intersection_street_1": None,
            "intersection_street_2": None, "quadrant": None, "facing_street": None, "deleted_at": None}


# SE384 TO-002 after the R1f walkthrough: all 15 flags claimed by IDR 001
WALKTHROUGH = [flags(MAIN_ST, SIDEWALK, "approved", 10, "383.44"), flags(ELM_ST, CONCRETE, "approved", 5, "3.01")]
TASK_ORDER_ROW = {"id": TASK_ORDER_ID, "project_id": PROJECT_ID, "task_order_number": "TO-002",
                  "issued_date": date(2026, 10, 1), "estimate_notes": None, "created_at": CREATED,
                  "updated_at": CREATED, "created_by": ADMIN_USER_ROW["uuid"], "deleted_at": None, "deleted_by": None,
                  "properties": [bundled(MAIN_ST), bundled(ELM_ST), bundled(OAK_ST)]}
PROPERTY_ROW = {**bundled(MAIN_ST), "id": MAIN_ST}


def line(data: dict, item_no: str) -> dict:
    """
    Find a pay item's line in a rollup's JSON.
    Takes the rollup and the item number.
    Returns the line.
    """
    return next(item for item in data["pay_items"] if item["item_no"] == item_no)


# ---------------------------------------------------------------------------
# The statements
# ---------------------------------------------------------------------------

class TestStatements:
    def test_the_flag_rows_are_scoped_to_the_project_and_its_properties(self):
        with patch(PROGRESS, return_value=[]) as run:
            assert list_flag_progress(PROJECT_ID, [MAIN_ST]) == []
        sql, params = run.call_args.args
        assert params == (PROJECT_ID, PROJECT_ID, [MAIN_ST], [MAIN_ST])
        assert "p.id = ANY(%s::uuid[])" in sql

    def test_in_flight_is_any_open_idr_of_the_project_with_the_flag_attributed(self):
        with patch(PROGRESS, return_value=[]) as run:
            list_flag_progress(PROJECT_ID, None)
        sql = " ".join(run.call_args.args[0].split())
        assert "SELECT DISTINCT f.id" in sql
        assert "i.status IN ('draft', 'submitted', 'stage1_review', 'stage2_review')" in sql
        assert "a.action IS DISTINCT FROM 'exclude' AND f.claimed_by_idr_id IS NULL" in sql
        assert "i.task_order_id = %s" not in sql

    def test_a_claimed_flag_counts_whatever_its_state_and_unmapped_only_while_live(self):
        with patch(PROGRESS, return_value=[]) as run:
            list_flag_progress(PROJECT_ID, None)
        sql = " ".join(run.call_args.args[0].split())
        assert "AND (f.claimed_by_idr_id IS NOT NULL OR f.deleted_at IS NULL" in sql
        assert "WHERE s.amount IS NOT NULL OR s.live" in sql

    def test_the_pay_items_are_those_in_a_unit_a_flag_measures(self):
        with patch(CONTRACT_ITEMS, return_value=PAY_ITEMS) as run:
            assert list_flag_pay_items(PROJECT_ID, FLAG_UNITS) == PAY_ITEMS
        sql, params = run.call_args.args
        assert params == (PROJECT_ID, ["SF", "SY", "CY", "EA"])
        assert "= ANY(%s)" in sql


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/progress-rollup
# ---------------------------------------------------------------------------

class TestProjectRollup:
    def test_the_walkthrough_is_100_percent_approved(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=WALKTHROUGH) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["scope"] == {"project_id": PROJECT_ID, "task_order_id": None, "property_id": None}
        assert line(data, "4.13 AAS")["approved"] == {"quantity": 383.44, "flag_count": 10, "value": 4325.2}
        assert line(data, "73.21AC")["approved"]["quantity"] == 3.01
        assert line(data, "73.21AC")["unit"] == "CY"
        assert line(data, "4.13 AAS")["in_flight"]["flag_count"] == line(data, "4.13 AAS")["unclaimed"]["flag_count"] == 0
        assert data["overall"]["approved_pct"] == 100.0
        assert data["unmapped_flag_count"] == 0 and data["unmapped_by_property"] == []
        assert "properties" not in data
        assert run.call_args.args[1][2] is None  # the whole project

    def test_an_unlocked_idr_turns_its_flags_unclaimed(self, admin_client):
        rows = [flags(MAIN_ST, SIDEWALK, "unclaimed", 10, "383.44"), flags(ELM_ST, CONCRETE, "unclaimed", 5, "3.01")]
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=rows):
            data = admin_client.get(URL).json()["data"]
        assert line(data, "4.13 AAS")["approved"]["flag_count"] == 0
        assert line(data, "4.13 AAS")["unclaimed"]["flag_count"] == 10
        assert data["overall"]["approved_pct"] == 0.0

    def test_unmapped_flags_are_listed_by_property(self, admin_client):
        rows = WALKTHROUGH[1:] + [flags(MAIN_ST, SIDEWALK, "approved", 9, "350"), flags(MAIN_ST, None, "unmapped", 1, "0")]
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=rows):
            data = admin_client.get(URL).json()["data"]
        assert data["unmapped_flag_count"] == 1
        assert data["unmapped_by_property"] == [
            {"property_id": str(MAIN_ST), "display_address": "138-22 227 Street", "flag_count": 1}]
        assert line(data, "4.13 AAS")["approved"]["flag_count"] == 9

    def test_nothing_surveyed_has_a_null_percent(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=[]):
            data = admin_client.get(URL).json()["data"]
        assert data["overall"] == {"approved_value": 0.0, "live_total_value": 0.0, "approved_pct": None}
        assert data["pay_items"] == []

    def test_include_empty_lists_every_pay_item_a_flag_can_fill(self, admin_client):
        rows = WALKTHROUGH[:1]
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS),                 patch(PROGRESS, return_value=rows):
            narrow = admin_client.get(URL).json()["data"]
            full = admin_client.get(URL, params={"include_empty": "true"}).json()["data"]
        assert [item["item_no"] for item in narrow["pay_items"]] == ["4.13 AAS"]
        assert [item["item_no"] for item in full["pay_items"]] == ["4.13 AAS", "73.21AC"]
        assert line(full, "73.21AC")["live_total"] == {"quantity": 0.0, "flag_count": 0, "value": 0.0}

    def test_a_task_order_filter_reads_its_bundled_properties(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(PROGRESS, return_value=WALKTHROUGH) as run:
            response = admin_client.get(URL, params={"task_order_id": str(TASK_ORDER_ID)})
        assert response.status_code == 200
        assert response.json()["data"]["scope"]["task_order_id"] == str(TASK_ORDER_ID)
        assert run.call_args.args[1][2] == [MAIN_ST, ELM_ST, OAK_ST]

    def test_a_property_filter_reads_that_property(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROPS, return_value=[PROPERTY_ROW]), patch(PROGRESS, return_value=WALKTHROUGH[:1]) as run:
            response = admin_client.get(URL, params={"property_id": str(MAIN_ST)})
        assert response.status_code == 200
        assert run.call_args.args[1][2] == [MAIN_ST]

    def test_both_filters_narrow_to_the_property_in_the_task_order(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PROGRESS, return_value=[]) as run:
            admin_client.get(URL, params={"task_order_id": str(TASK_ORDER_ID), "property_id": str(MAIN_ST)})
        assert run.call_args.args[1][2] == [MAIN_ST]

    def test_a_property_outside_the_task_order_is_an_empty_rollup(self, admin_client):
        outside = {**TASK_ORDER_ROW, "properties": [bundled(ELM_ST)]}
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(TASKS, return_value=[outside]), patch(PROPS, return_value=[PROPERTY_ROW]), \
                patch(PROGRESS, return_value=[]) as run:
            response = admin_client.get(URL, params={"task_order_id": str(TASK_ORDER_ID), "property_id": str(MAIN_ST)})
        assert response.status_code == 200
        assert run.call_args.args[1][2] == []
        assert response.json()["data"]["overall"]["approved_pct"] is None

    @pytest.mark.parametrize("task_order", [
        None, {**TASK_ORDER_ROW, "project_id": "HWS0023"}, {**TASK_ORDER_ROW, "deleted_at": CREATED}])
    def test_a_task_order_that_isnt_a_live_one_of_the_project_is_404(self, admin_client, task_order):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(TASKS, return_value=[] if task_order is None else [task_order]), patch(PROGRESS) as run:
            response = admin_client.get(URL, params={"task_order_id": str(TASK_ORDER_ID)})
        assert response.status_code == 404
        assert response.json()["detail"] == "Task order not found"
        run.assert_not_called()

    @pytest.mark.parametrize("found", [
        None, {**PROPERTY_ROW, "project_id": "HWS0023"}, {**PROPERTY_ROW, "deleted_at": CREATED}])
    def test_a_property_that_isnt_a_live_one_of_the_project_is_404(self, admin_client, found):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), \
                patch(PROPS, return_value=[] if found is None else [found]), patch(PROGRESS) as run:
            response = admin_client.get(URL, params={"property_id": str(MAIN_ST)})
        assert response.status_code == 404
        assert response.json()["detail"] == "Property not found"
        run.assert_not_called()

    def test_a_user_on_the_project_reads_it(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=ON_PROJECT), \
                patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), patch(PROGRESS, return_value=WALKTHROUGH):
            assert inspector.get(URL).status_code == 200

    def test_a_user_who_isnt_on_the_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, side_effect=OFF_PROJECT), patch(PROGRESS) as run:
            response = inspector.get(URL)
        assert response.status_code == 403
        run.assert_not_called()

    def test_an_unknown_project_is_404(self, admin_client):
        with patch(PROJECTS, return_value=[]), patch(PROGRESS) as run:
            assert admin_client.get(URL).status_code == 404
        run.assert_not_called()

    def test_a_demo_user_reads_their_own_project(self, demo_client):
        with patch(PROJECTS, return_value=[{**PROJECT_ROW, "project_id": "DEMO01"}]), \
                patch(CONTRACT_ITEMS, return_value=[]), patch(PROGRESS, return_value=[]):
            assert demo_client.get("/v1/projects/DEMO01/progress-rollup").status_code == 200

    def test_a_demo_user_gets_404_for_another_project(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(PROGRESS) as run:
            assert demo_client.get(URL).status_code == 404
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client):
        with patch(PROJECTS, return_value=[PROJECT_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=None):
            assert admin_client.get(URL).status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/task-orders/{task_order_id}/progress-rollup
# ---------------------------------------------------------------------------

class TestTaskOrderRollup:
    def test_the_walkthrough_per_property_and_sites_started(self, admin_client):
        with patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=WALKTHROUGH) as run:
            response = admin_client.get(TO_URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["scope"] == {"project_id": PROJECT_ID, "task_order_id": str(TASK_ORDER_ID), "property_id": None}
        assert data["overall"]["approved_pct"] == 100.0
        assert (data["site_count"], data["sites_started"]) == (3, 2)
        assert [entry["display_address"] for entry in data["properties"]] == [
            "138-22 227 Street", "350 Main St", "7 Oak St"]
        main = data["properties"][0]
        assert line(main, "4.13 AAS")["approved"] == line(data, "4.13 AAS")["approved"]
        assert data["properties"][2]["overall"]["approved_pct"] is None
        assert data["properties"][2]["pay_items"] == []
        assert run.call_args.args[1] == (PROJECT_ID, PROJECT_ID, [MAIN_ST, ELM_ST, OAK_ST], [MAIN_ST, ELM_ST, OAK_ST])

    def test_include_empty_lists_every_pay_item_for_each_property(self, admin_client):
        with patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS),                 patch(PROGRESS, return_value=WALKTHROUGH):
            data = admin_client.get(TO_URL, params={"include_empty": "true"}).json()["data"]
        assert [len(entry["pay_items"]) for entry in data["properties"]] == [2, 2, 2]

    def test_a_deleted_task_order_uses_its_bundle_as_it_stands(self, admin_client):
        with patch(TASKS, return_value=[{**TASK_ORDER_ROW, "deleted_at": CREATED}]), \
                patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), patch(PROGRESS, return_value=WALKTHROUGH):
            assert admin_client.get(TO_URL).status_code == 200

    def test_an_unknown_task_order_is_404(self, admin_client):
        with patch(TASKS, return_value=[]), patch(PROGRESS) as run:
            assert admin_client.get(TO_URL).status_code == 404
        run.assert_not_called()

    def test_a_user_who_isnt_on_its_project_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[]), \
                patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(PROGRESS) as run:
            assert inspector.get(TO_URL).status_code == 403
        run.assert_not_called()

    def test_a_user_on_its_project_reads_it(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS, return_value=[{"?column?": 1}]), \
                patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(CONTRACT_ITEMS, return_value=PAY_ITEMS), \
                patch(PROGRESS, return_value=WALKTHROUGH):
            assert inspector.get(TO_URL).status_code == 200

    def test_a_demo_user_gets_404_for_another_projects(self, demo_client):
        with patch(PROJECTS, return_value=[]), patch(TASKS, return_value=[TASK_ORDER_ROW]), patch(PROGRESS) as run:
            assert demo_client.get(TO_URL).status_code == 404
        run.assert_not_called()
