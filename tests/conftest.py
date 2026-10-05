import os

# The app won't start without a signing key; tests use a throwaway one unless the environment already has one
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-key-not-for-any-deployment")

from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402
from uuid import UUID  # noqa: E402

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from api.index import app  # noqa: E402
from api.schemas.auth import UserOut  # noqa: E402
from api.services.auth import auth_provider  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """
    Starlette TestClient wrapping the FastAPI app.
    Shared across the entire test session for speed.
    """
    with TestClient(app) as c:
        yield c


# The admin the auth fixtures sign in as: a row as api.queries.users.get_user_by_uuid returns it
# They have a signature on file, so they can submit
ADMIN_USER_ROW = {"uuid": UUID("a0000000-0000-4000-8000-000000000001"), "email": "admin@icid.local",
                  "first_name": "Ada", "last_name": "Admin", "client_id": "C00001", "role": "admin", "is_demo": False,
                  "signature_path": "users/a0000000-0000-4000-8000-000000000001/signature.png",
                  "signature_type": "drawn", "signature_set_at": datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)}
# The same admin before they set a signature
UNSIGNED_USER_ROW = {**ADMIN_USER_ROW, "signature_path": None, "signature_type": None, "signature_set_at": None}


@pytest.fixture
def admin_token() -> str:
    """
    A valid bearer token for the test admin (ADMIN_USER_ROW).
    Returns the encoded JWT.
    """
    return auth_provider.issue_token(UserOut.model_validate(ADMIN_USER_ROW))


@pytest.fixture
def admin_client(admin_token):
    """
    A TestClient signed in as the test admin: every request carries the admin's bearer token, and the token's
    user lookup returns ADMIN_USER_ROW without touching the query layer other tests patch.
    """
    with patch("api.services.auth.get_user_by_uuid", return_value=ADMIN_USER_ROW):
        with TestClient(app, headers={"Authorization": f"Bearer {admin_token}"}) as c:
            yield c


# A demo user, as POST /v1/auth/demo makes them: no role, no password, assigned to DEMO01 only
DEMO_USER_ROW = {"uuid": UUID("d0000000-0000-4000-8000-000000000004"),
                 "email": "demo-d0000000-0000-4000-8000-000000000004@icid.local", "first_name": "Demo",
                 "last_name": None, "client_id": "C00001", "role": None, "is_demo": True}


@pytest.fixture
def demo_token() -> str:
    """
    A valid bearer token for the test demo user (DEMO_USER_ROW).
    Returns the encoded JWT.
    """
    return auth_provider.issue_token(UserOut.model_validate(DEMO_USER_ROW))


@pytest.fixture
def demo_client(demo_token):
    """
    A TestClient signed in as the test demo user: every request carries their bearer token, and the token's user
    lookup returns DEMO_USER_ROW without touching the query layer other tests patch.
    """
    with patch("api.services.auth.get_user_by_uuid", return_value=DEMO_USER_ROW):
        with TestClient(app, headers={"Authorization": f"Bearer {demo_token}"}) as c:
            yield c


@pytest.fixture
def unsigned_client(admin_token):
    """
    A TestClient signed in as the test admin with no signature on file (UNSIGNED_USER_ROW).
    """
    with patch("api.services.auth.get_user_by_uuid", return_value=UNSIGNED_USER_ROW):
        with TestClient(app, headers={"Authorization": f"Bearer {admin_token}"}) as c:
            yield c
