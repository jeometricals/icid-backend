import os

# The app won't start without a signing key; tests use a throwaway one unless the environment already has one
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-key-not-for-any-deployment")

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from api.index import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """
    Starlette TestClient wrapping the FastAPI app.
    Shared across the entire test session for speed.
    """
    with TestClient(app) as c:
        yield c
