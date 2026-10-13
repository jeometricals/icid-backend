from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.contract_items import (
    clear_project_pay_items, create_project_pay_item, delete_project_pay_item, get_project_pay_item,
    get_project_pay_item_by_key, list_project_pay_items, update_project_pay_item,
)
from api.services import auth
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# /v1/admin/projects/{project_id}/pay-items
# Mock rows are dicts, as run_query returns them: a contract item under the names an admin sees, its NUMERIC
# columns as Decimal. Every route first looks the project up (api.queries.projects), then works on
# icid.contract_items (api.queries.contract_items).
# ---------------------------------------------------------------------------

PROJECT_ID = "HWS0023"
URL = f"/v1/admin/projects/{PROJECT_ID}/pay-items"
PROJECTS = "api.queries.projects.run_query"
ITEMS = "api.queries.contract_items.run_query"
CRON_SECRET = "cron-secret-for-tests-only"
CRON = {"Authorization": f"Bearer {CRON_SECRET}"}

ITEM_ID = "7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4e"
OTHER_ID = "8b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5f"
ITEM_URL = f"{URL}/{ITEM_ID}"
PROJECT_ROW = {"project_id": PROJECT_ID, "project_name": "S/W Queens 2025", "project_description": "Sidewalks",
               "registration_code": "2024123457", "borough": "Queens", "status": "Construction; Active"}
ITEM_ROW = {"pay_item_uuid": UUID(ITEM_ID), "pay_item_ref": "4.13 AAS", "description": '4" CONCRETE SIDEWALK',
            "unit": "SF", "quantity": Decimal("9500.00"), "unit_price": Decimal("14.50"), "budget_code": "12345",
            "spec_section": "4.13"}
ITEM_JSON = {"pay_item_uuid": ITEM_ID, "pay_item_ref": "4.13 AAS", "description": '4" CONCRETE SIDEWALK',
             "unit": "SF", "quantity": 9500.0, "unit_price": 14.5, "budget_code": "12345", "spec_section": "4.13"}
OTHER_ROW = {**ITEM_ROW, "pay_item_uuid": UUID(OTHER_ID), "budget_code": "SE0944(CONED-CONS", "spec_section": None}
BODY = {"pay_item_ref": "4.13 AAS", "description": '4" CONCRETE SIDEWALK', "unit": "SF", "quantity": 9500,
        "unit_price": 14.5, "budget_code": "12345"}
CONFLICT = "A pay item with this ref and budget code already exists for this project"
# A regular user: signed in, no admin role
INSPECTOR_ROW = {**ADMIN_USER_ROW, "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})
NO_PROJECT = (404, {"detail": "Project not found"})


@pytest.fixture
def project():
    """
    The project in the URL exists: the project lookup returns it.
    Yields the patched run_query of the projects queries.
    """
    with patch(PROJECTS, return_value=[PROJECT_ROW]) as run:
        yield run


@pytest.fixture
def no_project():
    """
    The project in the URL doesn't exist: the project lookup finds nothing.
    """
    with patch(PROJECTS, return_value=[]) as run:
        yield run


@pytest.fixture
def cron_secret(monkeypatch):
    """
    Give the app a CRON_SECRET for one test.
    """
    monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)


class TestListPayItems:
    def test_a_project_without_items_gives_an_empty_list(self, admin_client, project):
        with patch(ITEMS, return_value=[]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        assert response.json() == {"status": "success", "message": "Pay items for project HWS0023", "data": []}
        assert project.call_args.args[1] == (PROJECT_ID,) and run.call_args.args[1] == (PROJECT_ID,)

    def test_the_items_come_back_in_the_statements_order_as_numbers(self, admin_client, project):
        with patch(ITEMS, return_value=[ITEM_ROW, OTHER_ROW]):
            data = admin_client.get(URL).json()["data"]
        assert data == [ITEM_JSON, {**ITEM_JSON, "pay_item_uuid": OTHER_ID, "budget_code": "SE0944(CONED-CONS",
                                    "spec_section": None}]

    def test_the_statement_sorts_by_budget_code_then_ref(self, admin_client, project):
        with patch(ITEMS, return_value=[]) as run:
            admin_client.get(URL)
        sql = run.call_args.args[0]
        assert "FROM icid.contract_items ci" in sql and "WHERE ci.project_id = %s" in sql
        assert "ORDER BY ci.budget_code, ci.item_no;" in sql
        for column in ("ci.contract_item_id AS pay_item_uuid", "ci.item_no AS pay_item_ref", "ci.description",
                       "ci.unit", "ci.bid_quantity AS quantity", "ci.bid_unit_price AS unit_price", "ci.budget_code",
                       "ci.spec_section"):
            assert column in sql
        assert "spec_items" not in sql

    def test_an_unknown_project_is_404(self, admin_client, no_project):
        with patch(ITEMS) as run:
            response = admin_client.get(URL)
        assert (response.status_code, response.json()) == NO_PROJECT
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client, project):
        with patch(ITEMS, return_value=None):
            assert admin_client.get(URL).status_code == 500

    def test_a_user_who_isnt_an_admin_is_forbidden_before_anything_is_read(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = inspector.get(URL)
        assert (response.status_code, response.json()) == FORBIDDEN
        projects.assert_not_called()
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(PROJECTS) as projects, patch(ITEMS) as run:
            assert demo_client.get(URL).status_code == 403
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS) as projects, patch(ITEMS) as run:
            assert client.get(URL, headers=CRON).status_code == 401
        projects.assert_not_called()
        run.assert_not_called()


class TestCreatePayItem:
    def test_a_full_body_creates_the_item(self, admin_client, project):
        with patch(ITEMS, return_value=[ITEM_ROW]) as run:
            response = admin_client.post(URL, json={**BODY, "spec_section": "4.13"})
        assert response.status_code == 201 and response.json() == {"data": ITEM_JSON}
        assert run.call_count == 1
        assert run.call_args.args[1] == (PROJECT_ID, "4.13 AAS", '4" CONCRETE SIDEWALK', "SF", Decimal("9500"),
                                         Decimal("14.5"), "12345", "4.13")

    def test_the_statement_inserts_into_icid_contract_items_and_skips_a_taken_key(self, admin_client, project):
        with patch(ITEMS, return_value=[ITEM_ROW]) as run:
            admin_client.post(URL, json=BODY)
        sql = run.call_args.args[0]
        assert "INSERT INTO icid.contract_items AS ci" in sql
        assert "(project_id, item_no, description, unit, bid_quantity, bid_unit_price, budget_code, spec_section)" in sql
        assert "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)" in sql
        assert "ON CONFLICT (project_id, item_no, budget_code) DO NOTHING" in sql and "RETURNING" in sql
        assert "spec_item_id" not in sql

    def test_without_a_spec_section_it_is_none(self, admin_client, project):
        with patch(ITEMS, return_value=[{**ITEM_ROW, "spec_section": None}]) as run:
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 201 and response.json()["data"]["spec_section"] is None
        assert run.call_args.args[1][7] is None

    @pytest.mark.parametrize("spec_section", ["", "   ", None])
    def test_a_blank_spec_section_is_none(self, admin_client, project, spec_section):
        with patch(ITEMS, return_value=[{**ITEM_ROW, "spec_section": None}]) as run:
            assert admin_client.post(URL, json={**BODY, "spec_section": spec_section}).status_code == 201
        assert run.call_args.args[1][7] is None

    def test_the_same_ref_under_two_budget_codes_is_two_items(self, admin_client, project):
        with patch(ITEMS, side_effect=[[ITEM_ROW], [OTHER_ROW]]) as run:
            first = admin_client.post(URL, json=BODY)
            second = admin_client.post(URL, json={**BODY, "budget_code": "SE0944(CONED-CONS"})
        assert (first.status_code, second.status_code) == (201, 201)
        assert first.json()["data"]["pay_item_uuid"] != second.json()["data"]["pay_item_uuid"]
        assert second.json()["data"]["budget_code"] == "SE0944(CONED-CONS"
        assert [call.args[1][1] for call in run.call_args_list] == ["4.13 AAS", "4.13 AAS"]
        assert [call.args[1][6] for call in run.call_args_list] == ["12345", "SE0944(CONED-CONS"]

    def test_a_taken_ref_and_budget_code_is_409_naming_the_item_that_has_them(self, admin_client, project):
        with patch(ITEMS, side_effect=[[], [ITEM_ROW]]) as run:
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 409
        assert response.json() == {"detail": CONFLICT, "existing_pay_item_uuid": ITEM_ID}
        assert run.call_count == 2
        lookup_sql, lookup_params = run.call_args.args
        assert "ci.project_id = %s AND ci.item_no = %s AND ci.budget_code = %s" in lookup_sql
        assert lookup_params == (PROJECT_ID, "4.13 AAS", "12345")

    def test_a_taken_key_whose_item_is_gone_again_is_500(self, admin_client, project):
        with patch(ITEMS, side_effect=[[], []]):
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to create pay item"}

    def test_text_is_trimmed_the_unit_goes_to_capitals_and_the_budget_code_stays_as_typed(self, admin_client, project):
        body = {**BODY, "pay_item_ref": "  4.13 aas ", "description": " 4\" Concrete Sidewalk\t", "unit": " l.f. ",
                "budget_code": " se0944(Coned-Cons ", "spec_section": " 4.13 "}
        with patch(ITEMS, return_value=[ITEM_ROW]) as run:
            assert admin_client.post(URL, json=body).status_code == 201
        params = run.call_args.args[1]
        assert params[1:4] == ("4.13 aas", '4" Concrete Sidewalk', "L.F.")
        assert params[6:] == ("se0944(Coned-Cons", "4.13")

    @pytest.mark.parametrize("change,detail", [
        ({"pay_item_ref": ""}, "pay_item_ref cannot be blank"),
        ({"pay_item_ref": "   "}, "pay_item_ref cannot be blank"),
        ({"pay_item_ref": "r" * 33}, "pay_item_ref cannot be longer than 32 characters"),
        ({"description": " \t"}, "description cannot be blank"),
        ({"description": "d" * 501}, "description cannot be longer than 500 characters"),
        ({"unit": ""}, "unit cannot be blank"),
        ({"unit": "u" * 17}, "unit cannot be longer than 16 characters"),
        ({"budget_code": "  "}, "budget_code cannot be blank"),
        ({"budget_code": "b" * 51}, "budget_code cannot be longer than 50 characters"),
        ({"spec_section": "s" * 33}, "spec_section cannot be longer than 32 characters"),
        ({"quantity": -1}, "quantity cannot be negative"),
        ({"quantity": "-0.0000000001"}, "quantity cannot be negative"),
        ({"quantity": "1.12345678901"}, "quantity cannot have more than 10 decimal places"),
        ({"quantity": "1000000000000000"}, "quantity is too large"),
        ({"unit_price": -0.01}, "unit_price cannot be negative"),
        ({"unit_price": "1.1234567"}, "unit_price cannot have more than 6 decimal places"),
    ])
    def test_a_value_that_doesnt_fit_is_400_and_nothing_is_written(self, admin_client, project, change, detail):
        with patch(ITEMS) as run:
            response = admin_client.post(URL, json={**BODY, **change})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_values_at_the_limits_and_zero_are_taken(self, admin_client, project):
        body = {"pay_item_ref": "r" * 32, "description": "d" * 500, "unit": "u" * 16, "budget_code": "b" * 50,
                "spec_section": "s" * 32, "quantity": "0", "unit_price": "0.000000"}
        with patch(ITEMS, return_value=[ITEM_ROW]) as run:
            assert admin_client.post(URL, json=body).status_code == 201
            precise = {**BODY, "quantity": "1234.1234567890", "unit_price": "0.123456"}
            assert admin_client.post(URL, json=precise).status_code == 201
        assert run.call_args.args[1][4:6] == (Decimal("1234.1234567890"), Decimal("0.123456"))

    def test_trailing_zeros_arent_counted_as_decimal_places(self, admin_client, project):
        with patch(ITEMS, return_value=[ITEM_ROW]):
            assert admin_client.post(URL, json={**BODY, "unit_price": "14.5000000000"}).status_code == 201

    @pytest.mark.parametrize("missing", ["pay_item_ref", "description", "unit", "quantity", "unit_price", "budget_code"])
    def test_a_missing_required_field_is_422(self, admin_client, project, missing):
        with patch(ITEMS) as run:
            response = admin_client.post(URL, json={key: value for key, value in BODY.items() if key != missing})
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_quantity_that_isnt_a_number_is_422(self, admin_client, project):
        with patch(ITEMS) as run:
            assert admin_client.post(URL, json={**BODY, "quantity": "a lot"}).status_code == 422
        run.assert_not_called()

    def test_an_unknown_project_is_404(self, admin_client, no_project):
        with patch(ITEMS) as run:
            response = admin_client.post(URL, json=BODY)
        assert (response.status_code, response.json()) == NO_PROJECT
        run.assert_not_called()

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = inspector.post(URL, json=BODY)
        assert (response.status_code, response.json()) == FORBIDDEN
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS) as projects, patch(ITEMS) as run:
            assert client.post(URL, json=BODY, headers=CRON).status_code == 401
        projects.assert_not_called()
        run.assert_not_called()


class TestUpdatePayItem:
    def test_a_description_alone_changes_only_the_description(self, admin_client, project):
        with patch(ITEMS, return_value=[{**ITEM_ROW, "description": "4 INCH SIDEWALK"}]) as run:
            response = admin_client.patch(ITEM_URL, json={"description": " 4 INCH SIDEWALK "})
        assert response.status_code == 200
        assert response.json() == {"data": {**ITEM_JSON, "description": "4 INCH SIDEWALK"}}
        assert run.call_count == 1
        assert run.call_args.args[1] == (None, "4 INCH SIDEWALK", None, None, None, None, False, None,
                                         PROJECT_ID, UUID(ITEM_ID), None, None)

    def test_every_field_can_change_at_once(self, admin_client, project):
        body = {"pay_item_ref": "4.13 BAS", "description": "7 INCH", "unit": "sy", "quantity": "12.5",
                "unit_price": 3, "budget_code": "67890", "spec_section": "4.14"}
        with patch(ITEMS, return_value=[ITEM_ROW]) as run:
            assert admin_client.patch(ITEM_URL, json=body).status_code == 200
        assert run.call_args.args[1] == ("4.13 BAS", "7 INCH", "SY", Decimal("12.5"), Decimal("3"), "67890", True,
                                         "4.14", PROJECT_ID, UUID(ITEM_ID), "4.13 BAS", "67890")

    def test_one_statement_that_keeps_what_wasnt_sent_and_wont_collide(self, admin_client, project):
        with patch(ITEMS, return_value=[ITEM_ROW]) as run:
            admin_client.patch(ITEM_URL, json={"quantity": 0})
        sql = run.call_args.args[0]
        assert "UPDATE icid.contract_items ci" in sql and "updated_at = now()" in sql and "RETURNING" in sql
        for column in ("item_no", "description", "unit", "bid_quantity", "bid_unit_price", "budget_code"):
            assert f"{column} = COALESCE(%s, ci.{column})" in sql
        assert "spec_section = CASE WHEN %s THEN %s ELSE ci.spec_section END" in sql
        assert "WHERE ci.project_id = %s" in sql and "AND ci.contract_item_id = %s" in sql
        assert "AND NOT EXISTS (" in sql and "other.contract_item_id <> ci.contract_item_id" in sql
        assert "other.item_no = COALESCE(%s, ci.item_no)" in sql
        assert "other.budget_code = COALESCE(%s, ci.budget_code)" in sql
        assert run.call_args.args[1][3] == Decimal("0")

    @pytest.mark.parametrize("spec_section", ["", "  ", None])
    def test_a_blank_or_null_spec_section_clears_it(self, admin_client, project, spec_section):
        with patch(ITEMS, return_value=[{**ITEM_ROW, "spec_section": None}]) as run:
            response = admin_client.patch(ITEM_URL, json={"spec_section": spec_section})
        assert response.status_code == 200 and response.json()["data"]["spec_section"] is None
        assert run.call_args.args[1][6:8] == (True, None)

    def test_a_ref_and_budget_code_another_item_has_is_409(self, admin_client, project):
        with patch(ITEMS, side_effect=[[], [ITEM_ROW], [OTHER_ROW]]) as run:
            response = admin_client.patch(ITEM_URL, json={"budget_code": "SE0944(CONED-CONS"})
        assert response.status_code == 409
        assert response.json() == {"detail": CONFLICT, "existing_pay_item_uuid": OTHER_ID}
        assert run.call_count == 3
        # The key the item would have ended up with: its own ref, the new budget code
        assert run.call_args.args[1] == (PROJECT_ID, "4.13 AAS", "SE0944(CONED-CONS")

    def test_changing_an_item_to_the_key_it_already_has_is_not_a_conflict(self, admin_client, project):
        with patch(ITEMS, return_value=[ITEM_ROW]):
            response = admin_client.patch(ITEM_URL, json={"pay_item_ref": "4.13 AAS", "budget_code": "12345"})
        assert response.status_code == 200

    @pytest.mark.parametrize("body", [{}, {"description": None}, {"pay_item_ref": None, "quantity": None},
                                      {"project_id": "OTHER"}])
    def test_an_empty_body_is_400(self, admin_client, project, body):
        with patch(ITEMS) as run:
            response = admin_client.patch(ITEM_URL, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "Send at least one field to change"}
        run.assert_not_called()

    @pytest.mark.parametrize("body,detail", [
        ({"pay_item_ref": " "}, "pay_item_ref cannot be blank"),
        ({"description": "d" * 501}, "description cannot be longer than 500 characters"),
        ({"unit": ""}, "unit cannot be blank"),
        ({"budget_code": ""}, "budget_code cannot be blank"),
        ({"quantity": -5}, "quantity cannot be negative"),
        ({"unit_price": "0.0000001"}, "unit_price cannot have more than 6 decimal places"),
    ])
    def test_a_value_that_doesnt_fit_is_400(self, admin_client, project, body, detail):
        with patch(ITEMS) as run:
            response = admin_client.patch(ITEM_URL, json=body)
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_an_unknown_item_is_404(self, admin_client, project):
        with patch(ITEMS, side_effect=[[], []]) as run:
            response = admin_client.patch(ITEM_URL, json={"description": "X"})
        assert response.status_code == 404 and response.json() == {"detail": "Pay item not found"}
        assert run.call_count == 2

    def test_an_item_of_another_project_is_404(self, admin_client, project):
        # Both statements are tied to the project in the URL, so another project's item is not found
        with patch(ITEMS, side_effect=[[], []]) as run:
            response = admin_client.patch(f"/v1/admin/projects/DEMO01/pay-items/{ITEM_ID}", json={"description": "X"})
        assert response.status_code == 404 and response.json() == {"detail": "Pay item not found"}
        update, lookup = run.call_args_list
        assert update.args[1][8:10] == ("DEMO01", UUID(ITEM_ID)) and lookup.args[1] == ("DEMO01", UUID(ITEM_ID))
        assert "WHERE ci.project_id = %s AND ci.contract_item_id = %s;" in lookup.args[0]

    def test_an_unknown_project_is_404(self, admin_client, no_project):
        with patch(ITEMS) as run:
            response = admin_client.patch(ITEM_URL, json={"description": "X"})
        assert (response.status_code, response.json()) == NO_PROJECT
        run.assert_not_called()

    def test_an_item_id_that_isnt_a_uuid_is_422(self, admin_client, project):
        with patch(ITEMS) as run:
            assert admin_client.patch(f"{URL}/not-a-uuid", json={"description": "X"}).status_code == 422
        run.assert_not_called()

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = inspector.patch(ITEM_URL, json={"description": "X"})
        assert (response.status_code, response.json()) == FORBIDDEN
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS), patch(ITEMS) as run:
            assert client.patch(ITEM_URL, json={"description": "X"}, headers=CRON).status_code == 401
        run.assert_not_called()


class TestDeletePayItem:
    def test_an_item_of_the_project_is_deleted(self, admin_client, project):
        with patch(ITEMS, return_value=[{"contract_item_id": UUID(ITEM_ID)}]) as run:
            response = admin_client.delete(ITEM_URL)
        assert response.status_code == 204 and response.content == b""
        sql, params = run.call_args.args
        assert "DELETE FROM icid.contract_items ci" in sql
        assert "WHERE ci.project_id = %s AND ci.contract_item_id = %s" in sql
        assert params == (PROJECT_ID, UUID(ITEM_ID))

    def test_an_unknown_item_is_404(self, admin_client, project):
        with patch(ITEMS, return_value=[]):
            response = admin_client.delete(ITEM_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Pay item not found"}

    def test_an_unknown_project_is_404(self, admin_client, no_project):
        with patch(ITEMS) as run:
            response = admin_client.delete(ITEM_URL)
        assert (response.status_code, response.json()) == NO_PROJECT
        run.assert_not_called()

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = inspector.delete(ITEM_URL)
        assert (response.status_code, response.json()) == FORBIDDEN
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS), patch(ITEMS) as run:
            assert client.delete(ITEM_URL, headers=CRON).status_code == 401
        run.assert_not_called()


class TestClearPayItems:
    def test_every_item_of_the_project_is_deleted_and_counted(self, admin_client, project):
        with patch(ITEMS, return_value=[{"deleted_count": 23}]) as run:
            response = admin_client.delete(URL)
        assert response.status_code == 200
        assert response.json() == {"status": "success", "message": "Deleted 23 pay items from project HWS0023",
                                   "data": {"deleted_count": 23}}
        sql, params = run.call_args.args
        assert "DELETE FROM icid.contract_items ci" in sql and "WHERE ci.project_id = %s" in sql
        assert "contract_item_id = %s" not in sql and "SELECT count(*) AS deleted_count FROM gone;" in sql
        assert params == (PROJECT_ID,) and run.call_count == 1

    def test_a_project_without_items_deletes_none(self, admin_client, project):
        with patch(ITEMS, return_value=[{"deleted_count": 0}]):
            response = admin_client.delete(URL)
        assert response.status_code == 200 and response.json()["data"] == {"deleted_count": 0}

    def test_an_unknown_project_is_404(self, admin_client, no_project):
        with patch(ITEMS) as run:
            response = admin_client.delete(URL)
        assert (response.status_code, response.json()) == NO_PROJECT
        run.assert_not_called()

    def test_no_count_back_is_500(self, admin_client, project):
        with patch(ITEMS, return_value=None):
            assert admin_client.delete(URL).status_code == 500

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(PROJECTS) as projects, patch(ITEMS) as run:
            response = inspector.delete(URL)
        assert (response.status_code, response.json()) == FORBIDDEN
        projects.assert_not_called()
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(PROJECTS) as projects, patch(ITEMS) as run:
            assert client.delete(URL, headers=CRON).status_code == 401
        projects.assert_not_called()
        run.assert_not_called()


class TestPayItemQueries:
    def test_the_list_is_the_rows_or_none(self):
        with patch(ITEMS, return_value=[ITEM_ROW]):
            assert list_project_pay_items(PROJECT_ID) == [ITEM_ROW]
        with patch(ITEMS, return_value=None):
            assert list_project_pay_items(PROJECT_ID) is None

    def test_the_lookups_return_the_row_or_none(self):
        with patch(ITEMS, return_value=[ITEM_ROW]):
            assert get_project_pay_item(PROJECT_ID, UUID(ITEM_ID)) == ITEM_ROW
            assert get_project_pay_item_by_key(PROJECT_ID, "4.13 AAS", "12345") == ITEM_ROW
        with patch(ITEMS, return_value=[]):
            assert get_project_pay_item(PROJECT_ID, UUID(ITEM_ID)) is None
            assert get_project_pay_item_by_key(PROJECT_ID, "4.13 AAS", "12345") is None

    def test_create_and_update_return_the_row_or_none(self):
        args = (PROJECT_ID, "4.13 AAS", "X", "SF", Decimal("1"), Decimal("2"), "12345", None)
        with patch(ITEMS, return_value=[ITEM_ROW]):
            assert create_project_pay_item(*args) == ITEM_ROW
            assert update_project_pay_item(PROJECT_ID, UUID(ITEM_ID), {"unit": "LF"}) == ITEM_ROW
        with patch(ITEMS, return_value=[]):
            assert create_project_pay_item(*args) is None
            assert update_project_pay_item(PROJECT_ID, UUID(ITEM_ID), {"unit": "LF"}) is None

    def test_delete_says_whether_a_row_went(self):
        with patch(ITEMS, return_value=[{"contract_item_id": UUID(ITEM_ID)}]):
            assert delete_project_pay_item(PROJECT_ID, UUID(ITEM_ID)) is True
        with patch(ITEMS, return_value=[]):
            assert delete_project_pay_item(PROJECT_ID, UUID(ITEM_ID)) is False

    def test_clear_returns_the_count_or_none(self):
        with patch(ITEMS, return_value=[{"deleted_count": 4}]):
            assert clear_project_pay_items(PROJECT_ID) == 4
        with patch(ITEMS, return_value=None):
            assert clear_project_pay_items(PROJECT_ID) is None
