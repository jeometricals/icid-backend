from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import bcrypt
import jwt
import pytest
from fastapi import HTTPException

from api.core.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS, JWT_SECRET_KEY
from api.queries.users import get_user_by_email, get_user_by_uuid, get_user_for_auth
from api.schemas.auth import UserOut
from api.services.auth import AuthProvider, LocalAuthProvider, auth_provider, current_admin, current_user
from tests.conftest import ADMIN_USER_ROW

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
    def test_returns_204_signed_in_or_not(self, client):
        for headers in ({}, bearer(token()), bearer("garbage")):
            with patch(QUERY) as run:
                response = client.post("/v1/auth/logout", headers=headers)
            assert response.status_code == 204 and response.content == b""
            run.assert_not_called()


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
