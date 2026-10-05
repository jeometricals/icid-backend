import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import bcrypt
import jwt
import pytest
from fastapi import HTTPException

from api.core.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS, JWT_SECRET_KEY
from api.queries.report_attachments import list_storage_paths_for_demo_user
from api.queries.users import (
    create_demo_user, delete_demo_user_rows, get_user_by_email, get_user_by_uuid, get_user_for_auth,
)
from api.schemas.auth import UserOut
from api.services.auth import (
    AuthProvider, DemoUnavailableError, LocalAuthProvider, auth_provider, current_admin, current_user, optional_user,
    require_full_user,
)
from api.services.demo import delete_demo_user_cascade
from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the SELECTs in api/queries/users.py (get_user_for_auth adds password_hash).
# ---------------------------------------------------------------------------

PASSWORD = "correct horse"
# The seeded admin, as stored: the email keeps the case it was entered with
REZA_ROW = {"uuid": UUID("b0000000-0000-4000-8000-000000000002"), "email": "reza@icid.local", "first_name": "Reza",
            "last_name": None, "client_id": "C00001", "role": "admin", "is_demo": False}
REZA_AUTH_ROW = {**REZA_ROW, "password_hash": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(4)).decode()}
REZA_OUT = {"uuid": str(REZA_ROW["uuid"]), "email": "reza@icid.local", "first_name": "Reza", "last_name": None,
            "role": "admin", "is_demo": False}
INSPECTOR_ROW = {**REZA_ROW, "uuid": UUID("c0000000-0000-4000-8000-000000000003"), "email": "KhanG@magnoleng.pc",
                 "role": None}
QUERY = "api.queries.users.run_query"


def token(sub: object = REZA_ROW["uuid"], expires_in: int = 60, key: str = JWT_SECRET_KEY, **claims) -> str:
    """
    Sign a token by hand.
    Takes its subject, seconds until it expires (negative: already expired), the signing key and extra claims
    (None drops a claim).
    Returns the encoded JWT.
    """
    now = datetime.now(timezone.utc)
    payload = {"sub": None if sub is None else str(sub), "iat": now, "exp": now + timedelta(seconds=expires_in),
               **claims}
    return jwt.encode({k: v for k, v in payload.items() if v is not None}, key, algorithm=JWT_ALGORITHM)


def bearer(value: str) -> dict:
    """
    Build an Authorization header.
    Takes the token.
    Returns the headers dict.
    """
    return {"Authorization": f"Bearer {value}"}


# ---------------------------------------------------------------------------
# POST /v1/auth/login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_correct_credentials_return_a_token_and_the_user(self, client):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]):
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": PASSWORD})
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer" and body["expires_in"] == JWT_EXPIRY_SECONDS == 86400
        assert body["user"] == REZA_OUT
        claims = jwt.decode(body["access_token"], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        assert claims["sub"] == str(REZA_ROW["uuid"])
        assert claims["exp"] - claims["iat"] == JWT_EXPIRY_SECONDS

    def test_the_response_never_carries_the_password_hash(self, client):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]):
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": PASSWORD})
        assert "password_hash" not in response.text and REZA_AUTH_ROW["password_hash"] not in response.text
        assert "client_id" not in response.json()["user"]

    def test_the_email_is_matched_without_regard_to_case(self, client):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]) as run:
            response = client.post("/v1/auth/login", json={"email": " Reza@ICID.local ", "password": PASSWORD})
        assert response.status_code == 200
        sql, params = run.call_args.args
        assert "WHERE lower(u.email) = %s" in sql and params == ("reza@icid.local",)
        assert response.json()["user"]["email"] == "reza@icid.local"  # as stored

    def test_a_wrong_password_is_refused_without_a_token(self, client):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]):
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": "wrong horse"})
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password"}

    def test_an_unknown_email_is_refused_the_same_way(self, client):
        with patch(QUERY, return_value=[]):
            response = client.post("/v1/auth/login", json={"email": "nobody@icid.local", "password": PASSWORD})
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid email or password"}

    def test_an_unknown_email_still_costs_a_password_check(self, client):
        with patch(QUERY, return_value=[]), patch("api.services.auth.bcrypt.checkpw", return_value=False) as check:
            client.post("/v1/auth/login", json={"email": "nobody@icid.local", "password": PASSWORD})
        check.assert_called_once()  # so timing doesn't tell an unknown email from a wrong password

    def test_a_user_without_a_password_cant_sign_in(self, client):
        # the legacy demo user: no hash, and no password may match the stand-in hash either
        with patch(QUERY, return_value=[{**REZA_ROW, "password_hash": None}]), \
                patch("api.services.auth.bcrypt.checkpw", return_value=True):
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": PASSWORD})
        assert response.status_code == 401

    def test_a_password_bcrypt_wont_take_is_refused_not_an_error(self, client):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]):
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": "x" * 100})
        assert response.status_code == 401

    def test_an_unreadable_stored_hash_is_refused_not_an_error(self, client):
        with patch(QUERY, return_value=[{**REZA_ROW, "password_hash": "not-a-bcrypt-hash"}]):
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": PASSWORD})
        assert response.status_code == 401

    def test_a_short_password_fails_validation(self, client):
        with patch(QUERY) as run:
            response = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": "12345"})
        assert response.status_code == 422
        run.assert_not_called()

    def test_a_missing_field_fails_validation(self, client):
        assert client.post("/v1/auth/login", json={"email": "reza@icid.local"}).status_code == 422
        assert client.post("/v1/auth/login", json={"password": PASSWORD}).status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/auth/me
# ---------------------------------------------------------------------------

class TestMe:
    def test_a_valid_token_returns_its_user(self, client):
        with patch(QUERY, return_value=[REZA_ROW]) as run:
            response = client.get("/v1/auth/me", headers=bearer(token()))
        assert response.status_code == 200 and response.json() == REZA_OUT
        sql, params = run.call_args.args
        assert "WHERE u.uuid = %s" in sql and params == (REZA_ROW["uuid"],)

    def test_a_token_from_login_works(self, client):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]):
            issued = client.post("/v1/auth/login", json={"email": "reza@icid.local", "password": PASSWORD}).json()
        with patch(QUERY, return_value=[REZA_ROW]):
            response = client.get("/v1/auth/me", headers=bearer(issued["access_token"]))
        assert response.json() == issued["user"]

    def test_no_header_is_not_authenticated(self, client):
        response = client.get("/v1/auth/me")
        assert response.status_code == 401 and response.json() == {"detail": "Not authenticated"}
        assert response.headers["www-authenticate"] == "Bearer"

    def test_a_header_that_isnt_a_bearer_token_is_not_authenticated(self, client):
        for value in ("Basic cmV6YTpwdw==", token(), "bearer " + token()):
            response = client.get("/v1/auth/me", headers={"Authorization": value})
            assert response.status_code == 401 and response.json() == {"detail": "Not authenticated"}

    def test_garbage_is_an_invalid_token(self, client):
        response = client.get("/v1/auth/me", headers=bearer("garbage"))
        assert response.status_code == 401 and response.json() == {"detail": "Invalid token"}

    def test_an_expired_token_says_so(self, client):
        with patch(QUERY) as run:
            response = client.get("/v1/auth/me", headers=bearer(token(expires_in=-60)))
        assert response.status_code == 401 and response.json() == {"detail": "Token expired"}
        run.assert_not_called()

    @pytest.mark.parametrize("fault", [
        {"key": "another-key-entirely-not-the-apps-own"},  # signed by someone else
        {"sub": "not-a-uuid"},
        {"sub": None},
        {"exp": None},  # never expires: refused
    ])
    def test_a_token_that_doesnt_check_out_is_invalid(self, client, fault):
        with patch(QUERY) as run:
            response = client.get("/v1/auth/me", headers=bearer(token(**fault)))
        assert response.status_code == 401 and response.json() == {"detail": "Invalid token"}
        run.assert_not_called()

    def test_an_unsigned_token_is_invalid(self, client):
        unsigned = jwt.encode({"sub": str(REZA_ROW["uuid"]), "exp": datetime.now(timezone.utc) + timedelta(60)},
                              None, algorithm="none")
        response = client.get("/v1/auth/me", headers=bearer(unsigned))
        assert response.status_code == 401 and response.json() == {"detail": "Invalid token"}

    def test_a_token_for_a_user_who_is_gone_is_invalid(self, client):
        with patch(QUERY, return_value=[]):
            response = client.get("/v1/auth/me", headers=bearer(token()))
        assert response.status_code == 401 and response.json() == {"detail": "Invalid token"}


# ---------------------------------------------------------------------------
# POST /v1/auth/logout
# ---------------------------------------------------------------------------

class TestLogout:
    def test_returns_204_signed_in_or_not_and_deletes_nothing(self, client):
        for headers in ({}, bearer(token()), bearer("garbage"), bearer(token(expires_in=-60)),
                        {"Authorization": "Basic cmV6YTpwdw=="}):
            with patch(QUERY, return_value=[REZA_ROW]), patch("api.v1.auth.delete_demo_user_cascade") as delete:
                response = client.post("/v1/auth/logout", headers=headers)
            assert response.status_code == 204 and response.content == b""
            delete.assert_not_called()

    def test_a_non_demo_user_runs_no_delete_statement(self, client):
        with patch(QUERY, return_value=[REZA_ROW]) as users, \
                patch("api.queries.report_attachments.run_query") as attachments:
            assert client.post("/v1/auth/logout", headers=bearer(token())).status_code == 204
        assert users.call_count == 1 and "SELECT" in users.call_args.args[0]  # the token's user lookup, nothing else
        assert "DELETE" not in users.call_args.args[0]
        attachments.assert_not_called()

    def test_a_demo_user_is_deleted_with_everything_of_theirs(self, client):
        demo = bearer(token(DEMO_USER_ROW["uuid"]))
        paths = [{"storage_path": "r1/a_photo.jpg"}, {"storage_path": "r2/b_ticket.pdf"}]
        with patch(QUERY, side_effect=[[DEMO_USER_ROW], [{"uuid": DEMO_USER_ROW["uuid"]}]]) as users, \
                patch("api.queries.report_attachments.run_query", return_value=paths) as attachments, \
                patch("api.services.attachments.remove_files") as remove:
            response = client.post("/v1/auth/logout", headers=demo)
        assert response.status_code == 204 and response.content == b""
        # the files' paths are read and the files removed before the rows go
        assert attachments.call_args.args[1] == (DEMO_USER_ROW["uuid"],)
        assert [call.args[0] for call in remove.call_args_list] == [["r1/a_photo.jpg"], ["r2/b_ticket.pdf"]]
        sql, params = users.call_args.args
        assert params == (DEMO_USER_ROW["uuid"],)
        assert deleted_tables(sql) == CASCADE_ORDER

    def test_a_demo_users_delete_failing_still_signs_them_out(self, client, caplog):
        demo = bearer(token(DEMO_USER_ROW["uuid"]))
        with patch(QUERY, return_value=[DEMO_USER_ROW]), \
                patch("api.v1.auth.delete_demo_user_cascade", side_effect=RuntimeError("db down")) as delete:
            response = client.post("/v1/auth/logout", headers=demo)
        assert response.status_code == 204
        delete.assert_called_once_with(DEMO_USER_ROW["uuid"])
        assert "Could not delete demo user" in caplog.text

    def test_the_user_lookup_failing_still_returns_204(self, client):
        with patch(QUERY, side_effect=RuntimeError("db down")), patch("api.v1.auth.delete_demo_user_cascade") as delete:
            response = client.post("/v1/auth/logout", headers=bearer(token()))
        assert response.status_code == 204
        delete.assert_not_called()


# ---------------------------------------------------------------------------
# The dependencies, the provider and the fixtures H2+ reuse
# ---------------------------------------------------------------------------

class TestCurrentAdmin:
    def test_an_admin_passes(self):
        admin = UserOut.model_validate(REZA_ROW)
        assert current_admin(admin) is admin

    @pytest.mark.parametrize("role", [None, "inspector", "Admin", ""])
    def test_anyone_else_is_forbidden(self, role):
        with pytest.raises(HTTPException) as raised:
            current_admin(UserOut.model_validate({**INSPECTOR_ROW, "role": role}))
        assert (raised.value.status_code, raised.value.detail) == (403, "Admin access required")

    def test_it_reads_the_user_from_current_user(self):
        with patch(QUERY, return_value=[INSPECTOR_ROW]):
            user = current_user(f"Bearer {token(INSPECTOR_ROW['uuid'])}")
        assert user.email == "KhanG@magnoleng.pc" and user.role is None


class TestProvider:
    def test_the_app_uses_the_local_provider_behind_the_interface(self):
        assert isinstance(auth_provider, LocalAuthProvider) and isinstance(auth_provider, AuthProvider)
        assert AuthProvider.__abstractmethods__ == {"verify_credentials", "issue_token", "create_demo_user"}
        with pytest.raises(TypeError):
            AuthProvider()  # abstract: a provider must supply both methods

    def test_verify_credentials_returns_the_user_or_none(self):
        with patch(QUERY, return_value=[REZA_AUTH_ROW]):
            assert auth_provider.verify_credentials("reza@icid.local", PASSWORD) == UserOut.model_validate(REZA_ROW)
            assert auth_provider.verify_credentials("reza@icid.local", "wrong horse") is None


class TestUserQueries:
    def test_only_the_auth_lookup_selects_the_password_hash(self):
        with patch(QUERY, return_value=[REZA_ROW]) as run:
            assert get_user_by_email("Reza@ICID.local") == REZA_ROW
            assert get_user_by_uuid(REZA_ROW["uuid"]) == REZA_ROW
        for call in run.call_args_list:
            assert "password_hash" not in call.args[0] and "FROM icid.users u" in call.args[0]
        assert run.call_args_list[0].args[1] == ("reza@icid.local",)
        with patch(QUERY, return_value=[REZA_AUTH_ROW]) as run:
            assert get_user_for_auth("reza@icid.local") == REZA_AUTH_ROW
        assert "u.password_hash" in run.call_args.args[0] and "u.is_demo," in run.call_args.args[0]

    def test_no_match_is_none(self):
        with patch(QUERY, return_value=[]):
            assert (get_user_by_email("x@y.z"), get_user_for_auth("x@y.z"), get_user_by_uuid(REZA_ROW["uuid"])) == (
                None, None, None)


class TestFixtures:
    def test_admin_token_names_the_test_admin(self, admin_token):
        claims = jwt.decode(admin_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        assert claims["sub"] == str(ADMIN_USER_ROW["uuid"])

    def test_admin_client_is_signed_in_as_an_admin(self, admin_client):
        response = admin_client.get("/v1/auth/me")
        assert response.status_code == 200
        assert (response.json()["email"], response.json()["role"]) == ("admin@icid.local", "admin")


# ---------------------------------------------------------------------------
# Every /v1 route needs a signed-in user, except signing in and out
# ---------------------------------------------------------------------------

PUBLIC_ROUTES = {("POST", "/v1/auth/login"), ("POST", "/v1/auth/logout"), ("POST", "/v1/auth/demo"), ("GET", "/status"),
                 ("GET", "/debug/schema")}
PATH_VALUES = {"idr_id": "9b2d4f6a-8c1e-4a3b-9d5f-7e1a2b3c4d5e", "report_id": "e6f7a8b9-c0d1-4e2f-9a3b-4c5d6e7f8091",
               "attachment_id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "project_id": "HWS0023"}


def api_routes() -> list[tuple[str, str]]:
    """
    List the app's own routes (not the generated docs).
    Takes nothing.
    Returns (method, path template) pairs, sorted.
    """
    from fastapi.routing import APIRoute
    from api.index import app
    return sorted((method, route.path) for route in app.routes if isinstance(route, APIRoute)
                  for method in route.methods)


PROTECTED_ROUTES = [route for route in api_routes() if route not in PUBLIC_ROUTES]


class TestEveryRouteNeedsSignIn:
    def test_the_protected_routes_are_the_ones_expected(self):
        assert all(path.startswith("/v1/") for _, path in PROTECTED_ROUTES)
        assert [route for route in api_routes() if route[1].startswith("/v1/") and route in PUBLIC_ROUTES] == [
            ("POST", "/v1/auth/demo"), ("POST", "/v1/auth/login"), ("POST", "/v1/auth/logout")]
        assert len(PROTECTED_ROUTES) == 20
        for expected in (("GET", "/v1/projects/"), ("GET", "/v1/idrs/"), ("POST", "/v1/idrs/"),
                         ("GET", "/v1/idrs/{idr_id}"), ("POST", "/v1/idrs/{idr_id}/submit"),
                         ("GET", "/v1/idrs/{idr_id}/export"), ("GET", "/v1/users/"), ("GET", "/v1/contract_items/"),
                         ("POST", "/v1/idrs/{idr_id}/reports/{report_id}/attachments/upload-request")):
            assert expected in PROTECTED_ROUTES

    @pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
    def test_no_token_is_401_before_anything_is_read(self, client, method, path):
        with patch("api.db.runner.get_connection") as connect:
            response = client.request(method, path.format(**PATH_VALUES))
        assert response.status_code == 401 and response.json() == {"detail": "Not authenticated"}
        connect.assert_not_called()

    @pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
    def test_a_garbage_token_is_401(self, client, method, path):
        with patch("api.db.runner.get_connection") as connect:
            response = client.request(method, path.format(**PATH_VALUES), headers=bearer("garbage"))
        assert response.status_code == 401 and response.json() == {"detail": "Invalid token"}
        connect.assert_not_called()

    @pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
    def test_an_expired_token_is_401(self, client, method, path):
        with patch("api.db.runner.get_connection") as connect:
            response = client.request(method, path.format(**PATH_VALUES), headers=bearer(token(expires_in=-60)))
        assert response.status_code == 401 and response.json() == {"detail": "Token expired"}
        connect.assert_not_called()

    def test_the_old_user_id_query_parameter_doesnt_sign_anyone_in(self, client):
        # the hardcoded demo uuid the frontend used to send
        response = client.get("/v1/projects/?user_id=327d3ed2-a3d6-4235-9408-7fe721b12bed")
        assert response.status_code == 401

    def test_a_valid_token_for_a_user_who_is_gone_is_401_everywhere(self, client):
        with patch(QUERY, return_value=[]):
            response = client.get("/v1/idrs/", headers=bearer(token()))
        assert response.status_code == 401 and response.json() == {"detail": "Invalid token"}

    def test_status_stays_public(self, client):
        assert client.get("/status").status_code == 200


# ---------------------------------------------------------------------------
# Demo mode: POST /v1/auth/demo, the fences around a demo user, and their delete
# ---------------------------------------------------------------------------

CASCADE_ORDER = ["report_attachments", "idr_reports", "idrs", "project_users", "users"]
DEMO_OUT = {"uuid": str(DEMO_USER_ROW["uuid"]), "email": DEMO_USER_ROW["email"], "first_name": "Demo",
            "last_name": None, "role": None, "is_demo": True}
OTHER_DEMO_ROW = {**DEMO_USER_ROW, "uuid": UUID("d0000000-0000-4000-8000-000000000005"),
                  "email": "demo-d0000000-0000-4000-8000-000000000005@icid.local"}
SOMEONE_ELSES_IDR = {"idr_id": UUID(PATH_VALUES["idr_id"]), "reporter_uuid": REZA_ROW["uuid"]}
IDR_ROUTES = [route for route in PROTECTED_ROUTES if "{idr_id}" in route[1]]


def deleted_tables(sql: str) -> list[str]:
    """
    List the icid tables a statement (or a file's worth of them) deletes from, in the order written.
    Takes the SQL.
    Returns the table names.
    """
    return re.findall(r"DELETE FROM icid\.(\w+)", sql)


class TestStartDemo:
    def test_returns_a_token_and_the_new_demo_user(self, client):
        with patch(QUERY, return_value=[DEMO_USER_ROW]):
            response = client.post("/v1/auth/demo")
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer" and body["expires_in"] == JWT_EXPIRY_SECONDS  # as for login
        assert body["user"] == DEMO_OUT
        assert re.fullmatch(r"demo-[0-9a-f-]{36}@icid\.local", body["user"]["email"])
        claims = jwt.decode(body["access_token"], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        assert claims["sub"] == str(DEMO_USER_ROW["uuid"]) and claims["exp"] - claims["iat"] == JWT_EXPIRY_SECONDS
        assert "password_hash" not in response.text

    def test_needs_no_credentials_and_takes_no_body(self, client):
        with patch(QUERY, return_value=[DEMO_USER_ROW]):
            assert client.post("/v1/auth/demo").status_code == 200
            assert client.post("/v1/auth/demo", headers=bearer("garbage")).status_code == 200

    def test_the_user_and_the_demo01_assignment_are_one_statement(self, client):
        with patch(QUERY, return_value=[DEMO_USER_ROW]) as run:
            client.post("/v1/auth/demo")
        assert run.call_count == 1  # both rows or neither
        sql, params = run.call_args.args
        assert "INSERT INTO icid.users (uuid, email, first_name, client_id, is_demo)" in sql  # no password, no role
        assert "SELECT id.uuid, 'demo-' || id.uuid::text || '@icid.local', 'Demo', %s, true" in sql
        assert "uuid_generate_v4()" in sql
        assert "INSERT INTO icid.project_users (project_id, user_uuid, user_role)" in sql
        assert sql.index("INSERT INTO icid.users") < sql.index("INSERT INTO icid.project_users")
        assert params == ("C00001", "DEMO01", 200, "DEMO01", "Demo")

    def test_the_demo_user_can_use_their_token(self, client):
        with patch(QUERY, return_value=[DEMO_USER_ROW]):
            issued = client.post("/v1/auth/demo").json()
            response = client.get("/v1/auth/me", headers=bearer(issued["access_token"]))
        assert response.status_code == 200 and response.json() == DEMO_OUT

    def test_two_demos_are_two_users(self, client):
        with patch(QUERY, side_effect=[[DEMO_USER_ROW], [OTHER_DEMO_ROW]]) as run:
            first, second = client.post("/v1/auth/demo").json(), client.post("/v1/auth/demo").json()
        assert run.call_count == 2  # each call inserts its own user and its own DEMO01 assignment
        assert first["user"]["uuid"] != second["user"]["uuid"] and first["user"]["email"] != second["user"]["email"]
        assert first["access_token"] != second["access_token"]
        assert "%s" not in run.call_args.args[0].split("FROM (SELECT uuid_generate_v4()")[0].split("SELECT id.uuid")[0]

    def test_a_demo_user_cant_sign_in_with_a_password(self, client):
        with patch(QUERY, return_value=[{**DEMO_USER_ROW, "password_hash": None}]):
            response = client.post("/v1/auth/login", json={"email": DEMO_USER_ROW["email"], "password": PASSWORD})
        assert response.status_code == 401

    def test_503_when_the_demo_project_is_missing(self, client):
        with patch(QUERY, return_value=[]), patch("api.queries.projects.run_query", return_value=[]):
            response = client.post("/v1/auth/demo")
        assert response.status_code == 503 and response.json() == {"detail": "Demo mode is not set up"}

    def test_503_when_too_many_demo_users_exist(self, client):
        with patch(QUERY, return_value=[]) as run, \
                patch("api.queries.projects.run_query", return_value=[{"project_id": "DEMO01"}]):
            response = client.post("/v1/auth/demo")
        assert response.status_code == 503 and response.json() == {"detail": "Demo mode is busy, try again later"}
        assert "(SELECT count(*) FROM icid.users d WHERE d.is_demo = true) < %s" in run.call_args.args[0]

    def test_the_provider_raises_when_a_demo_user_cant_be_made(self):
        with patch(QUERY, return_value=[]), patch("api.queries.projects.run_query", return_value=[]):
            with pytest.raises(DemoUnavailableError):
                auth_provider.create_demo_user()
        with patch(QUERY, return_value=[DEMO_USER_ROW]):
            assert create_demo_user("C00001", "DEMO01", "Demo", 200) == DEMO_USER_ROW
            assert auth_provider.create_demo_user() == UserOut.model_validate(DEMO_USER_ROW)


class TestDemoFences:
    @pytest.mark.parametrize("method,path", IDR_ROUTES)
    def test_a_demo_user_cant_reach_someone_elses_idr(self, demo_client, method, path):
        with patch("api.queries.idrs.run_query", return_value=[SOMEONE_ELSES_IDR]) as idrs, \
                patch("api.queries.idr_reports.run_query") as reports, \
                patch("api.queries.report_attachments.run_query") as attachments:
            response = demo_client.request(method, path.format(**PATH_VALUES))
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}
        assert idrs.call_count == 1  # the fence's own lookup; the route never ran
        reports.assert_not_called()
        attachments.assert_not_called()

    def test_the_fence_covers_every_route_under_an_idr(self):
        assert len(IDR_ROUTES) == 13
        assert ("GET", "/v1/idrs/{idr_id}/export") in IDR_ROUTES
        assert ("GET", "/v1/idrs/{idr_id}/reports/{report_id}/attachments") in IDR_ROUTES

    def test_a_demo_user_reaches_their_own_idr(self, demo_client):
        own = {**SOMEONE_ELSES_IDR, "reporter_uuid": DEMO_USER_ROW["uuid"]}
        with patch("api.queries.idrs.run_query", return_value=[own]), \
                patch("api.queries.idr_reports.run_query", return_value=[]):
            response = demo_client.get(f"/v1/idrs/{PATH_VALUES['idr_id']}/reports/{PATH_VALUES['report_id']}/attachments")
        assert response.status_code == 404 and response.json() == {"detail": "Report not found in this IDR"}  # past the fence

    def test_an_idr_that_doesnt_exist_is_the_routes_own_404(self, demo_client):
        with patch("api.queries.idrs.run_query", return_value=[]):
            response = demo_client.get(f"/v1/idrs/{PATH_VALUES['idr_id']}")
        assert response.status_code == 404 and response.json() == {"detail": "IDR not found"}

    def test_a_malformed_idr_id_is_still_422(self, demo_client):
        with patch("api.queries.idrs.run_query") as idrs:
            assert demo_client.get("/v1/idrs/not-a-uuid").status_code == 422
        idrs.assert_not_called()

    def test_other_users_are_not_fenced(self, admin_client):
        with patch("api.queries.idrs.run_query", return_value=[]) as idrs:
            response = admin_client.get(f"/v1/idrs/{PATH_VALUES['idr_id']}")
        assert response.status_code == 404 and idrs.call_count == 1  # the route's lookup only, no fence query

    def test_a_demo_user_cant_list_users(self, demo_client):
        with patch(QUERY) as run:
            response = demo_client.get("/v1/users/")
        assert response.status_code == 403 and response.json() == {"detail": "Demo mode: not available"}
        run.assert_not_called()

    @pytest.mark.parametrize("url", ["/v1/projects/HWS0023", "/v1/contract_items/?project_id=HWS0023"])
    def test_a_demo_user_cant_reach_a_project_they_arent_on(self, demo_client, url):
        with patch("api.queries.projects.run_query", return_value=[]) as projects, \
                patch("api.queries.contract_items.run_query") as items:
            response = demo_client.get(url)
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"}
        sql, params = projects.call_args.args
        assert "icid.project_users" in sql and params == (DEMO_USER_ROW["uuid"], "HWS0023")
        assert projects.call_count == 1
        items.assert_not_called()

    def test_a_demo_user_reaches_their_own_projects_contract_items(self, demo_client):
        with patch("api.queries.projects.run_query", return_value=[{"?column?": 1}]), \
                patch("api.queries.contract_items.run_query", return_value=[]):
            response = demo_client.get("/v1/contract_items/?project_id=DEMO01")
        assert response.status_code == 200 and response.json()["data"] == []

    def test_a_demo_user_lists_their_projects_without_a_fence_query(self, demo_client):
        with patch("api.queries.projects.run_query", return_value=[]) as projects:
            response = demo_client.get("/v1/projects/")
        assert response.status_code == 200 and projects.call_count == 1
        assert projects.call_args.args[1] == (DEMO_USER_ROW["uuid"],)

    def test_an_admin_reaches_any_project(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=[]) as projects, \
                patch("api.queries.contract_items.run_query", return_value=[]):
            assert admin_client.get("/v1/contract_items/?project_id=HWS0023").status_code == 200
        projects.assert_not_called()


class TestDemoDependencies:
    def test_require_full_user_passes_everyone_but_demo_users(self):
        for row in (REZA_ROW, INSPECTOR_ROW):
            user = UserOut.model_validate(row)
            assert require_full_user(user) is user
        with pytest.raises(HTTPException) as raised:
            require_full_user(UserOut.model_validate(DEMO_USER_ROW))
        assert (raised.value.status_code, raised.value.detail) == (403, "Demo mode: submit is disabled")

    def test_optional_user_is_the_user_or_none(self):
        with patch(QUERY, return_value=[REZA_ROW]):
            assert optional_user(f"Bearer {token()}") == UserOut.model_validate(REZA_ROW)
        assert optional_user(None) is None and optional_user("Bearer garbage") is None
        assert optional_user(f"Bearer {token(expires_in=-60)}") is None


class TestDemoDelete:
    def test_the_delete_is_one_statement_children_first_and_only_ever_a_demo_user(self):
        with patch(QUERY, return_value=[{"uuid": DEMO_USER_ROW["uuid"]}]) as run:
            assert delete_demo_user_rows(DEMO_USER_ROW["uuid"]) == [{"uuid": DEMO_USER_ROW["uuid"]}]
        assert run.call_count == 1
        sql, params = run.call_args.args
        assert params == (DEMO_USER_ROW["uuid"],) and sql.count("%s") == 1
        assert deleted_tables(sql) == CASCADE_ORDER
        # every delete hangs off the one row picked here, so a user who isn't a demo user loses nothing
        assert "SELECT u.uuid FROM icid.users u WHERE u.uuid = %s AND u.is_demo = true" in sql
        assert "DELETE FROM icid.users u WHERE u.uuid IN (SELECT uuid FROM demo) AND u.is_demo = true" in sql
        assert sql.count("FROM demo") >= 4

    def test_the_file_listing_is_only_ever_a_demo_users(self):
        with patch("api.queries.report_attachments.run_query", return_value=[]) as run:
            assert list_storage_paths_for_demo_user(DEMO_USER_ROW["uuid"]) == []
        sql, params = run.call_args.args
        assert "JOIN icid.users u ON u.uuid = %s AND u.is_demo = true" in sql and params == (DEMO_USER_ROW["uuid"],)

    def test_cascade_reports_whether_a_demo_user_went(self):
        with patch("api.queries.report_attachments.run_query", return_value=[]), \
                patch(QUERY, return_value=[{"uuid": DEMO_USER_ROW["uuid"]}]):
            assert delete_demo_user_cascade(DEMO_USER_ROW["uuid"]) is True
        with patch("api.queries.report_attachments.run_query", return_value=[]), patch(QUERY, return_value=[]):
            assert delete_demo_user_cascade(REZA_ROW["uuid"]) is False  # not a demo user: nothing matched

    def test_rows_still_go_when_the_files_cant_be_listed_or_removed(self):
        with patch("api.queries.report_attachments.run_query", return_value=None), \
                patch(QUERY, return_value=[{"uuid": DEMO_USER_ROW["uuid"]}]) as users, \
                patch("api.services.attachments.remove_files") as remove:
            assert delete_demo_user_cascade(DEMO_USER_ROW["uuid"]) is True
        remove.assert_not_called()
        assert users.call_count == 1
        with patch("api.queries.report_attachments.run_query", return_value=[{"storage_path": "r1/a.jpg"}]), \
                patch(QUERY, return_value=[{"uuid": DEMO_USER_ROW["uuid"]}]), \
                patch("api.services.attachments.remove_files", side_effect=RuntimeError("storage down")):
            assert delete_demo_user_cascade(DEMO_USER_ROW["uuid"]) is True

    def test_the_daily_cleanup_deletes_in_the_same_order(self):
        migration = (Path(__file__).resolve().parents[2] / "migrations" / "014_demo_cleanup.sql").read_text("utf-8")
        body = migration[migration.index("CREATE OR REPLACE FUNCTION"):migration.index("COMMIT;")]
        assert deleted_tables(body) == CASCADE_ORDER
