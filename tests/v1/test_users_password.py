from unittest.mock import patch

import bcrypt
import pytest

from api.queries.users import update_user_password
from api.services.auth import auth_provider
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# POST /v1/users/me/password
# Mock rows are dicts, as run_query returns them. The route runs two statements: the sign-in read of the user
# with their hash, then the UPDATE.
# ---------------------------------------------------------------------------

URL = "/v1/users/me/password"
QUERY = "api.queries.users.run_query"
CURRENT = "old-pass-1"
NEW = "new-pass-2"
BODY = {"current_password": CURRENT, "new_password": NEW}
# The signed-in user as sign-in reads them, with the hash of CURRENT (few rounds, to keep the tests quick)
STORED_ROW = {**ADMIN_USER_ROW,
              "password_hash": bcrypt.hashpw(CURRENT.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")}
OK = {"status": "success", "message": "Password changed", "data": None}


class TestChangeOwnPassword:
    def test_the_right_current_password_and_a_valid_new_one_change_it(self, admin_client):
        with patch(QUERY, side_effect=[[STORED_ROW], [ADMIN_USER_ROW]]) as run:
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 200 and response.json() == OK
        assert run.call_count == 2
        read_sql, read_params = run.call_args_list[0].args
        assert "u.password_hash" in read_sql and read_params == ("admin@icid.local",)
        sql, (password_hash, uuid) = run.call_args_list[1].args
        assert "UPDATE icid.users u" in sql and "SET password_hash = %s" in sql and "WHERE u.uuid = %s" in sql
        assert "updated_at = now()" in sql and "u.password_hash" not in sql.split("RETURNING")[1]
        assert uuid == ADMIN_USER_ROW["uuid"]
        assert password_hash.startswith("$2b$") and bcrypt.checkpw(NEW.encode("utf-8"), password_hash.encode("utf-8"))

    def test_the_user_signs_in_with_the_new_password_and_not_the_old(self, admin_client):
        with patch(QUERY, side_effect=[[STORED_ROW], [ADMIN_USER_ROW]]) as run:
            admin_client.post(URL, json=BODY)
        stored = {**ADMIN_USER_ROW, "password_hash": run.call_args.args[1][0]}
        with patch("api.services.auth.get_user_for_auth", return_value=stored):
            assert auth_provider.verify_credentials("admin@icid.local", NEW).uuid == ADMIN_USER_ROW["uuid"]
            assert auth_provider.verify_credentials("admin@icid.local", CURRENT) is None

    def test_nothing_secret_comes_back(self, admin_client):
        with patch(QUERY, side_effect=[[STORED_ROW], [ADMIN_USER_ROW]]):
            response = admin_client.post(URL, json=BODY)
        assert NEW not in response.text and CURRENT not in response.text and "$2b$" not in response.text

    def test_any_role_can_change_their_own(self):
        inspector = {**ADMIN_USER_ROW, "role": None}
        with signed_in(inspector) as client, patch(QUERY, side_effect=[[{**STORED_ROW, "role": None}], [inspector]]):
            assert client.post(URL, json=BODY).status_code == 200

    def test_a_wrong_current_password_is_400_and_nothing_is_written(self, admin_client):
        with patch(QUERY, side_effect=[[STORED_ROW]]) as run:
            response = admin_client.post(URL, json={**BODY, "current_password": "not-the-one"})
        assert response.status_code == 400 and response.json() == {"detail": "Current password is incorrect"}
        assert run.call_count == 1

    @pytest.mark.parametrize("stored", [[{**STORED_ROW, "password_hash": None}], []])
    def test_a_user_without_a_password_is_refused_as_a_wrong_one(self, admin_client, stored):
        with patch(QUERY, side_effect=[stored]) as run:
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 400 and response.json() == {"detail": "Current password is incorrect"}
        assert run.call_count == 1

    def test_a_blank_current_password_is_400(self, admin_client):
        with patch(QUERY) as run:
            response = admin_client.post(URL, json={**BODY, "current_password": ""})
        assert response.status_code == 400 and response.json() == {"detail": "current_password cannot be blank"}
        run.assert_not_called()

    @pytest.mark.parametrize("new_password,detail", [
        ("12345", "new_password must be at least 6 characters"),
        ("", "new_password must be at least 6 characters"),
        ("p" * 73, "new_password cannot be longer than 72 bytes"),
        ("é" * 37, "new_password cannot be longer than 72 bytes"),
    ])
    def test_a_new_password_that_doesnt_fit_is_400(self, admin_client, new_password, detail):
        with patch(QUERY) as run:
            response = admin_client.post(URL, json={**BODY, "new_password": new_password})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_a_new_password_of_72_bytes_is_taken_as_typed(self, admin_client):
        with patch(QUERY, side_effect=[[STORED_ROW], [ADMIN_USER_ROW]]) as run:
            response = admin_client.post(URL, json={**BODY, "new_password": " " + "p" * 70 + " "})
        assert response.status_code == 200
        assert bcrypt.checkpw((" " + "p" * 70 + " ").encode("utf-8"), run.call_args.args[1][0].encode("utf-8"))

    def test_the_same_password_again_is_400(self, admin_client):
        with patch(QUERY) as run:
            response = admin_client.post(URL, json={"current_password": CURRENT, "new_password": CURRENT})
        assert response.status_code == 400 and response.json() == {"detail": "New password must differ from current"}
        run.assert_not_called()

    @pytest.mark.parametrize("missing", ["current_password", "new_password"])
    def test_a_missing_field_is_422(self, admin_client, missing):
        with patch(QUERY) as run:
            response = admin_client.post(URL, json={key: value for key, value in BODY.items() if key != missing})
        assert response.status_code == 422
        run.assert_not_called()

    def test_no_token_is_401(self, client):
        with patch(QUERY) as run:
            response = client.post(URL, json=BODY)
        assert response.status_code == 401 and response.json() == {"detail": "Not authenticated"}
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(QUERY) as run:
            response = demo_client.post(URL, json=BODY)
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: not available"}
        run.assert_not_called()

    def test_a_user_gone_before_the_write_is_500(self, admin_client):
        with patch(QUERY, side_effect=[[STORED_ROW], []]):
            response = admin_client.post(URL, json=BODY)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to change password"}


class TestUpdateUserPasswordQuery:
    def test_it_returns_the_row_or_none_for_no_match(self):
        with patch(QUERY, return_value=[ADMIN_USER_ROW]):
            assert update_user_password(ADMIN_USER_ROW["uuid"], "hash") == ADMIN_USER_ROW
        with patch(QUERY, return_value=[]):
            assert update_user_password(ADMIN_USER_ROW["uuid"], "hash") is None
