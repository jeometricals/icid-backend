from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import bcrypt
import pytest

from api.queries.users import create_user, list_users_for_admin, update_user_names
from api.services import auth
from api.services.auth import auth_provider, hash_password
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET and POST /v1/admin/users, and PATCH /v1/admin/users/{user_id}
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

    def test_create_returns_the_row_or_none_when_the_email_is_taken(self):
        with patch(QUERY, return_value=[USER_ROW]):
            assert create_user("a@icid.local", "A", None, "hash", None, "C00001") == USER_ROW
        with patch(QUERY, return_value=[]):
            assert create_user("a@icid.local", "A", None, "hash", None, "C00001") is None


# ---------------------------------------------------------------------------
# POST /v1/admin/users
# ---------------------------------------------------------------------------

NEW_ID = "c0000000-0000-4000-8000-000000000003"
FULL_BODY = {"email": "New.User@icid.local", "first_name": "Nora", "last_name": "New", "password": "test1234",
             "is_admin": False}
MINIMUM_BODY = {"email": "New.User@icid.local", "first_name": "Nora", "password": "test1234"}


def created(**fields) -> list[dict]:
    """
    Build what the INSERT returns for the new user.
    Takes the columns that differ from a regular user named Nora New.
    Returns a one-row list.
    """
    return [{"uuid": UUID(NEW_ID), "email": "New.User@icid.local", "first_name": "Nora", "last_name": "New",
             "client_id": "C00001", "role": None, "is_demo": False, "signature_path": None, "signature_type": None,
             "signature_set_at": None, **fields}]


class TestCreateAdminUser:
    def test_a_full_body_creates_the_user(self, admin_client):
        with patch(QUERY, return_value=created()) as run:
            response = admin_client.post(URL, json=FULL_BODY)
        assert response.status_code == 201
        assert response.json() == {"data": {"uuid": NEW_ID, "email": "New.User@icid.local", "first_name": "Nora",
                                            "last_name": "New", "role": None, "is_demo": False,
                                            "signature_set_at": None, "has_signature": False}}
        assert run.call_count == 1
        email, first_name, last_name, client_id, password_hash, role = run.call_args.args[1]
        assert (email, first_name, last_name, client_id, role) == ("New.User@icid.local", "Nora", "New", "C00001", None)

    def test_the_minimum_body_has_no_last_name_and_no_admin_role(self, admin_client):
        with patch(QUERY, return_value=created(last_name=None)) as run:
            response = admin_client.post(URL, json=MINIMUM_BODY)
        assert response.status_code == 201
        assert (response.json()["data"]["last_name"], response.json()["data"]["role"]) == (None, None)
        params = run.call_args.args[1]
        assert (params[2], params[5]) == (None, None)

    def test_is_admin_makes_an_admin(self, admin_client):
        with patch(QUERY, return_value=created(role="admin")) as run:
            response = admin_client.post(URL, json={**FULL_BODY, "is_admin": True})
        assert response.status_code == 201 and response.json()["data"]["role"] == "admin"
        assert run.call_args.args[1][5] == "admin"

    def test_the_password_is_stored_as_a_bcrypt_hash_never_as_typed(self, admin_client):
        with patch(QUERY, return_value=created()) as run:
            response = admin_client.post(URL, json=FULL_BODY)
        params = run.call_args.args[1]
        assert "test1234" not in params
        assert params[4].startswith("$2b$") and bcrypt.checkpw(b"test1234", params[4].encode("utf-8"))
        assert "test1234" not in response.text and "password" not in response.text

    def test_the_new_user_can_sign_in_with_the_password(self, admin_client):
        with patch(QUERY, return_value=created()) as run:
            admin_client.post(URL, json=FULL_BODY)
        stored = {**created()[0], "password_hash": run.call_args.args[1][4]}
        with patch("api.services.auth.get_user_for_auth", return_value=stored):
            assert auth_provider.verify_credentials("new.user@icid.local", "test1234").uuid == UUID(NEW_ID)
            assert auth_provider.verify_credentials("new.user@icid.local", "test12345") is None

    def test_the_statement_inserts_a_non_demo_user_and_skips_a_taken_email(self, admin_client):
        with patch(QUERY, return_value=created()) as run:
            admin_client.post(URL, json=FULL_BODY)
        sql = run.call_args.args[0]
        assert "INSERT INTO icid.users AS u (email, first_name, last_name, client_id, password_hash, role, is_demo)" in sql
        assert "VALUES (%s, %s, %s, %s, %s, %s, false)" in sql
        assert "ON CONFLICT (lower(email)) DO NOTHING" in sql and "RETURNING" in sql
        assert "u.password_hash" not in sql.split("RETURNING")[1]

    def test_the_email_and_names_are_trimmed_and_the_email_keeps_its_capitals(self, admin_client):
        body = {"email": "  New.User@icid.local ", "first_name": " Nora ", "last_name": " New\t", "password": " pass w "}
        with patch(QUERY, return_value=created()) as run:
            assert admin_client.post(URL, json=body).status_code == 201
        params = run.call_args.args[1]
        assert params[:3] == ("New.User@icid.local", "Nora", "New")
        assert bcrypt.checkpw(b" pass w ", params[4].encode("utf-8"))  # the password is taken as typed

    @pytest.mark.parametrize("last_name", ["", "   ", None])
    def test_a_blank_last_name_is_stored_as_none(self, admin_client, last_name):
        with patch(QUERY, return_value=created(last_name=None)) as run:
            assert admin_client.post(URL, json={**FULL_BODY, "last_name": last_name}).status_code == 201
        assert run.call_args.args[1][2] is None

    def test_a_taken_email_is_409_naming_the_user_who_has_it(self, admin_client):
        existing = {**USER_ROW, "email": "new.user@icid.local"}
        with patch(QUERY, side_effect=[[], [existing]]) as run:
            response = admin_client.post(URL, json={**FULL_BODY, "email": "NEW.USER@icid.local"})
        assert response.status_code == 409
        assert response.json() == {"detail": "A user with this email already exists", "existing_user_uuid": USER_ID}
        assert run.call_count == 2
        lookup_sql, lookup_params = run.call_args.args
        assert "lower(u.email) = %s" in lookup_sql and lookup_params == ("new.user@icid.local",)

    def test_a_taken_email_whose_user_is_gone_again_is_500(self, admin_client):
        with patch(QUERY, side_effect=[[], []]):
            response = admin_client.post(URL, json=FULL_BODY)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to create user"}

    @pytest.mark.parametrize("change,detail", [
        ({"email": ""}, "email cannot be blank"),
        ({"email": "   "}, "email cannot be blank"),
        ({"email": "no-at-sign.local"}, "email is not a valid email address"),
        ({"email": "a@"}, "email is not a valid email address"),
        ({"email": "a" * 244 + "@icid.local"}, "email cannot be longer than 254 characters"),
        ({"first_name": ""}, "first_name cannot be blank"),
        ({"first_name": "  "}, "first_name cannot be blank"),
        ({"first_name": "n" * 101}, "first_name cannot be longer than 100 characters"),
        ({"last_name": "n" * 101}, "last_name cannot be longer than 100 characters"),
        ({"password": "12345"}, "password must be at least 6 characters"),
        ({"password": ""}, "password must be at least 6 characters"),
        ({"password": "p" * 73}, "password cannot be longer than 72 bytes"),
        ({"password": "é" * 37}, "password cannot be longer than 72 bytes"),
    ])
    def test_a_value_that_doesnt_fit_is_400_and_nothing_is_written(self, admin_client, change, detail):
        with patch(QUERY) as run:
            response = admin_client.post(URL, json={**FULL_BODY, **change})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_values_at_the_limits_are_taken(self, admin_client):
        body = {"email": "a" * 243 + "@icid.local", "first_name": "n" * 100, "last_name": "n" * 100,
                "password": "p" * 72}
        with patch(QUERY, return_value=created()):
            assert admin_client.post(URL, json=body).status_code == 201

    @pytest.mark.parametrize("missing", ["email", "first_name", "password"])
    def test_a_missing_required_field_is_422(self, admin_client, missing):
        body = {key: value for key, value in FULL_BODY.items() if key != missing}
        with patch(QUERY) as run:
            assert admin_client.post(URL, json=body).status_code == 422
        run.assert_not_called()

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.post(URL, json=FULL_BODY)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(QUERY) as run:
            assert demo_client.post(URL, json=FULL_BODY).status_code == 403
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, monkeypatch):
        monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)
        with patch(QUERY) as run:
            response = client.post(URL, json=FULL_BODY, headers={"Authorization": f"Bearer {CRON_SECRET}"})
        assert response.status_code == 401  # the secret is no user's token
        run.assert_not_called()


class TestHashPassword:
    def test_each_hash_is_salted_and_checks_out(self):
        first, second = hash_password("test1234"), hash_password("test1234")
        assert first != second
        assert bcrypt.checkpw(b"test1234", first.encode("utf-8")) and bcrypt.checkpw(b"test1234", second.encode("utf-8"))

    def test_a_password_over_72_bytes_is_refused_not_cut(self):
        assert hash_password("p" * 72).startswith("$2b$")
        with pytest.raises(ValueError):
            hash_password("p" * 73)
