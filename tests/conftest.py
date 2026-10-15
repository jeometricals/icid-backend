import os

# The app won't start without a signing key; tests use a throwaway one unless the environment already has one
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-key-not-for-any-deployment")

from contextlib import contextmanager  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402
from uuid import UUID  # noqa: E402

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from api.index import app  # noqa: E402
from api.schemas.auth import UserOut  # noqa: E402
from api.services.auth import auth_provider  # noqa: E402


def _no_idr_flags(sql: str, params: object = None) -> list:
    """
    Stand in for the IDR-flag statements (api.queries.idr_flags) in tests that don't patch them: no IDR covers any flag, and a deleted IDR's record of released claims is empty.
    Takes the statement and its parameters.
    Returns the rows: one empty record for released_claims, none otherwise.
    """
    if "released_flag_ids" in sql:
        return [{"status_before": None, "released": [], "taken": []}]
    return []


@pytest.fixture(scope="session", autouse=True)
def no_idr_flags():
    """
    Patch the IDR-flag statements for the whole session (R1f; module-scoped fixtures that build an export run too), so the IDR routes that now read an IDR's flags (approve, delete, un-delete, report saves, the export) see none unless a test patches api.queries.idr_flags.run_query itself; an approved IDR's frozen flag rollup (api.services.idr_flags.list_flag_quantities) is empty too.
    Yields the mock of the statements.
    """
    with patch("api.queries.idr_flags.run_query", side_effect=_no_idr_flags) as run,             patch("api.services.idr_flags.list_flag_quantities", return_value=[]):
        yield run


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


@contextmanager
def signed_in(user_row: dict):
    """
    A TestClient signed in as any user, for tests that need someone other than the fixtures' admin and demo user.
    Takes the row get_user_by_uuid returns for them.
    Yields the client; every request carries their bearer token.
    """
    token = auth_provider.issue_token(UserOut.model_validate(user_row))
    with patch("api.services.auth.get_user_by_uuid", return_value=user_row):
        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
            yield c
