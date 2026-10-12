from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest

from api.queries.users import list_users_for_admin, update_user_names
from api.services import auth
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET /v1/admin/users and PATCH /v1/admin/users/{user_id}
# Mock rows are dicts, as run_query returns them: the columns sign-in reads about a user.
# ---------------------------------------------------------------------------

URL = "/v1/admin/users"
QUERY = "api.queries.users.run_query"
USER_ID = "b0000000-0000-4000-8000-000000000002"
USER_URL = f"{URL}/{USER_ID}"
CRON_SECRET = "cron-secret-for-tests-only"

USER_ROW = {"uuid": UUID(USER_ID), "email": "KhanG@magnoleng.pc", "first_name": "Genghis", "last_name": "Khan",
            "client_id": "C00001", "role": None, "is_demo": False, "signature_path": None, "signature_type": None,
            "signature_set_at": None}
USER_JSON = {"uuid": USER_ID, "email": "KhanG@magnoleng.pc", "first_name": "Genghis", "last_name": "Khan",
             "role": None, "is_demo": False, "signature_set_at": None, "has_signature": False}
# A regular user: signed in, no admin role
INSPECTOR_ROW = {**ADMIN_USER_ROW, "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})


def renamed(**names: str) -> list[dict]:
    """
    Build what the UPDATE returns for the test user after a rename.
    Takes the names that changed.
    Returns a one-row list.
    """
    return [{**USER_ROW, **names}]


class TestListAdminUsers:
    def test_an_admin_gets_every_user_demo_users_included(self, admin_client):
        with patch(QUERY, return_value=[ADMIN_USER_ROW, {**DEMO_USER_ROW, "signature_path": None}, USER_ROW]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert [user["email"] for user in data] == ["admin@icid.local", DEMO_USER_ROW["email"], "KhanG@magnoleng.pc"]
        assert data[2] == USER_JSON
        assert (data[0]["role"], data[0]["has_signature"]) == ("admin", True)
        assert data[0]["signature_set_at"] == datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z")
        assert data[1]["is_demo"] is True
        assert run.call_count == 1

    def test_nothing_secret_is_in_a_row(self, admin_client):
        with patch(QUERY, return_value=[{**ADMIN_USER_ROW, "password_hash": "$2b$12$x"}]):
            user = admin_client.get(URL).json()["data"][0]
        assert set(user) == set(USER_JSON)

    def test_the_statement_reads_icid_users_in_name_order_without_the_hash(self, admin_client):
        with patch(QUERY, return_value=[]) as run:
            assert admin_client.get(URL).json() == {"data": []}
        sql = run.call_args.args[0]
        assert "FROM icid.users u" in sql and "ORDER BY u.last_name, u.first_name, u.email;" in sql
        assert "password_hash" not in sql and "WHERE" not in sql

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.get(URL)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(QUERY) as run:
            assert demo_client.get(URL).status_code == 403
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, monkeypatch):
        monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)
        with patch(QUERY) as run:
            response = client.get(URL, headers={"Authorization": f"Bearer {CRON_SECRET}"})
            patched = client.patch(USER_URL, json={"first_name": "X"}, headers={"Authorization": f"Bearer {CRON_SECRET}"})
        assert response.status_code == 401 and patched.status_code == 401
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client):
        with patch(QUERY, return_value=None):
            response = admin_client.get(URL)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to fetch users"}


class TestUpdateAdminUser:
    def test_first_name_only(self, admin_client):
        with patch(QUERY, return_value=renamed(first_name="Temujin")) as run:
            response = admin_client.patch(USER_URL, json={"first_name": "Temujin"})
        assert response.status_code == 200
        assert response.json() == {"data": {**USER_JSON, "first_name": "Temujin"}}
        assert run.call_args.args[1] == ("Temujin", None, UUID(USER_ID))

    def test_last_name_only(self, admin_client):
        with patch(QUERY, return_value=renamed(last_name="Borjigin")) as run:
            response = admin_client.patch(USER_URL, json={"last_name": "Borjigin"})
        assert response.status_code == 200 and response.json()["data"]["last_name"] == "Borjigin"
        assert run.call_args.args[1] == (None, "Borjigin", UUID(USER_ID))

    def test_both_names(self, admin_client):
        with patch(QUERY, return_value=renamed(first_name="Temujin", last_name="Borjigin")) as run:
            response = admin_client.patch(USER_URL, json={"first_name": "Temujin", "last_name": "Borjigin"})
        assert response.status_code == 200
        assert response.json() == {"data": {**USER_JSON, "first_name": "Temujin", "last_name": "Borjigin"}}
        assert run.call_args.args[1] == ("Temujin", "Borjigin", UUID(USER_ID))

    def test_names_are_trimmed(self, admin_client):
        with patch(QUERY, return_value=renamed(first_name="Temujin")) as run:
            admin_client.patch(USER_URL, json={"first_name": "  Temujin \t", "last_name": " Khan "})
        assert run.call_args.args[1] == ("Temujin", "Khan", UUID(USER_ID))

    def test_one_statement_on_icid_users_that_keeps_a_name_not_sent(self, admin_client):
        with patch(QUERY, return_value=renamed(first_name="Temujin")) as run:
            admin_client.patch(USER_URL, json={"first_name": "Temujin"})
        assert run.call_count == 1
        sql = run.call_args.args[0]
        assert "UPDATE icid.users u" in sql and "WHERE u.uuid = %s" in sql and "RETURNING" in sql
        assert "first_name = COALESCE(%s, u.first_name)" in sql and "last_name = COALESCE(%s, u.last_name)" in sql
        assert "updated_at = now()" in sql and "password_hash" not in sql

    @pytest.mark.parametrize("body", [{}, {"first_name": None}, {"first_name": None, "last_name": None},
                                      {"email": "new@icid.local"}])
    def test_neither_name_is_400(self, admin_client, body):
        with patch(QUERY) as run:
            response = admin_client.patch(USER_URL, json=body)
        assert response.status_code == 400 and response.json() == {"detail": "Send first_name, last_name or both"}
        run.assert_not_called()

    @pytest.mark.parametrize("body,field", [({"first_name": ""}, "first_name"), ({"first_name": "   "}, "first_name"),
                                            ({"last_name": "\t\n"}, "last_name"),
                                            ({"first_name": "Temujin", "last_name": " "}, "last_name")])
    def test_a_blank_name_is_400(self, admin_client, body, field):
        with patch(QUERY) as run:
            response = admin_client.patch(USER_URL, json=body)
        assert response.status_code == 400 and response.json() == {"detail": f"{field} cannot be blank"}
        run.assert_not_called()

    def test_a_name_over_100_characters_is_400_and_one_of_100_is_taken(self, admin_client):
        with patch(QUERY, return_value=renamed(last_name="k" * 100)) as run:
            too_long = admin_client.patch(USER_URL, json={"last_name": "k" * 101})
            assert run.call_count == 0
            at_the_limit = admin_client.patch(USER_URL, json={"last_name": " " + "k" * 100 + " "})
        assert too_long.status_code == 400
        assert too_long.json() == {"detail": "last_name cannot be longer than 100 characters"}
        assert at_the_limit.status_code == 200

    def test_an_unknown_user_is_404(self, admin_client):
        with patch(QUERY, return_value=[]):
            response = admin_client.patch(USER_URL, json={"first_name": "Temujin"})
        assert response.status_code == 404 and response.json() == {"detail": "User not found"}

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.patch(USER_URL, json={"first_name": "Temujin"})
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(QUERY) as run:
            assert demo_client.patch(USER_URL, json={"first_name": "Temujin"}).status_code == 403
        run.assert_not_called()

    def test_a_demo_users_name_can_be_changed(self, admin_client):
        row = {**DEMO_USER_ROW, "signature_path": None, "last_name": "Tester"}
        with patch(QUERY, return_value=[row]):
            response = admin_client.patch(f"{URL}/{DEMO_USER_ROW['uuid']}", json={"last_name": "Tester"})
        assert response.status_code == 200
        assert (response.json()["data"]["is_demo"], response.json()["data"]["last_name"]) == (True, "Tester")

    def test_a_user_id_that_isnt_a_uuid_is_422(self, admin_client):
        with patch(QUERY) as run:
            assert admin_client.patch(f"{URL}/not-a-uuid", json={"first_name": "Temujin"}).status_code == 422
        run.assert_not_called()


class TestAdminUserQueries:
    def test_the_list_is_the_rows_or_none(self):
        with patch(QUERY, return_value=[USER_ROW]):
            assert list_users_for_admin() == [USER_ROW]
        with patch(QUERY, return_value=None):
            assert list_users_for_admin() is None

    def test_the_update_returns_the_row_or_none_for_no_match(self):
        with patch(QUERY, return_value=[USER_ROW]):
            assert update_user_names(UUID(USER_ID), "Genghis", None) == USER_ROW
        with patch(QUERY, return_value=[]):
            assert update_user_names(UUID(USER_ID), "Genghis", None) is None
