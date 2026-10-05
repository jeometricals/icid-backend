import json
from pathlib import Path
from unittest.mock import patch

import pytest

from api.queries.users import cleanup_abandoned_demo_users
from api.services import auth
from tests.conftest import ADMIN_USER_ROW

# ---------------------------------------------------------------------------
# POST / GET /v1/admin/cleanup-demos
# Mock rows are dicts, as run_query returns them: the cleanup function's result under its alias.
# ---------------------------------------------------------------------------

URL = "/v1/admin/cleanup-demos"
QUERY = "api.queries.users.run_query"
CRON_SECRET = "cron-secret-for-tests-only"
ROOT = Path(__file__).resolve().parents[2]


def cron(secret: str = CRON_SECRET) -> dict:
    """
    Build the header Vercel Cron sends.
    Takes the secret.
    Returns the headers dict.
    """
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
def cron_secret(monkeypatch):
    """
    Give the app a CRON_SECRET for one test.
    """
    monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)


class TestCleanupDemosAsAdmin:
    @pytest.mark.parametrize("method", ["POST", "GET"])
    def test_an_admin_runs_the_cleanup_and_gets_the_count(self, admin_client, method):
        with patch(QUERY, return_value=[{"purged_count": 3}]) as run:
            response = admin_client.request(method, URL)
        assert response.status_code == 200 and response.json() == {"purged_count": 3}
        assert run.call_count == 1
        assert run.call_args.args[0] == "SELECT icid.cleanup_abandoned_demo_users() AS purged_count;"

    def test_nothing_to_purge_is_zero_not_an_error(self, admin_client):
        with patch(QUERY, return_value=[{"purged_count": 0}]):
            response = admin_client.post(URL)
        assert response.status_code == 200 and response.json() == {"purged_count": 0}

    def test_a_signed_in_user_who_isnt_an_admin_is_forbidden(self, client):
        from tests.v1.test_auth import bearer, token
        inspector = {**ADMIN_USER_ROW, "role": None}
        with patch("api.services.auth.get_user_by_uuid", return_value=inspector), patch(QUERY) as run:
            response = client.post(URL, headers=bearer(token(ADMIN_USER_ROW["uuid"])))
        assert response.status_code == 403 and response.json() == {"detail": "Admin access required"}
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(QUERY) as run:
            response = demo_client.post(URL)
        assert response.status_code == 403
        run.assert_not_called()

    def test_no_token_is_401(self, client):
        with patch(QUERY) as run:
            response = client.post(URL)
        assert response.status_code == 401 and response.json() == {"detail": "Not authenticated"}
        run.assert_not_called()

    def test_the_function_returning_nothing_is_500(self, admin_client):
        with patch(QUERY, return_value=[]):
            response = admin_client.post(URL)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to clean up demo users"}


class TestCleanupDemosAsCron:
    def test_vercel_cron_gets_in_with_the_secret_on_a_get(self, client, cron_secret):
        with patch(QUERY, return_value=[{"purged_count": 2}]) as run, \
                patch("api.services.auth.get_user_by_uuid") as lookup:
            response = client.get(URL, headers=cron())
        assert response.status_code == 200 and response.json() == {"purged_count": 2}
        assert run.call_count == 1
        lookup.assert_not_called()  # no user behind the scheduler

    def test_the_secret_works_on_a_post_too(self, client, cron_secret):
        with patch(QUERY, return_value=[{"purged_count": 0}]):
            assert client.post(URL, headers=cron()).status_code == 200

    @pytest.mark.parametrize("header", [
        {"Authorization": "Bearer wrong-secret"},
        {"Authorization": f"Bearer {CRON_SECRET}x"},
        {"Authorization": f"Bearer x{CRON_SECRET}"},
        {"Authorization": CRON_SECRET},
        {"Authorization": f"bearer {CRON_SECRET}"},
        {"X-Cron-Secret": CRON_SECRET},
    ])
    def test_anything_but_the_exact_secret_is_refused(self, client, cron_secret, header):
        with patch(QUERY) as run:
            response = client.get(URL, headers=header)
        assert response.status_code == 401
        run.assert_not_called()

    def test_with_no_secret_configured_nothing_gets_in_as_the_scheduler(self, client, monkeypatch):
        monkeypatch.setattr(auth, "CRON_SECRET", "")
        with patch(QUERY) as run:
            for header in ({"Authorization": "Bearer "}, {"Authorization": "Bearer"}, {}):
                assert client.get(URL, headers=header).status_code == 401
        run.assert_not_called()

    def test_the_secret_opens_nothing_else(self, client, cron_secret):
        for method, url in (("GET", "/v1/users/"), ("GET", "/v1/projects/"), ("GET", "/v1/auth/me"),
                            ("POST", "/v1/signatures/confirm")):
            assert client.request(method, url, headers=cron()).status_code == 401

    def test_an_admin_still_gets_in_when_a_secret_is_set(self, admin_client, cron_secret):
        with patch(QUERY, return_value=[{"purged_count": 1}]):
            assert admin_client.post(URL).json() == {"purged_count": 1}


class TestCleanupSetup:
    def test_the_query_returns_the_count_or_none(self):
        with patch(QUERY, return_value=[{"purged_count": 4}]):
            assert cleanup_abandoned_demo_users() == 4
        with patch(QUERY, return_value=[]):
            assert cleanup_abandoned_demo_users() is None

    def test_it_calls_the_function_migration_014_creates(self):
        migration = (ROOT / "migrations" / "014_demo_cleanup.sql").read_text(encoding="utf-8")
        assert "CREATE OR REPLACE FUNCTION icid.cleanup_abandoned_demo_users()" in migration
        assert "RETURNS INTEGER" in migration

    def test_vercel_runs_it_daily_at_the_routes_real_path(self):
        from api.index import app
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        assert config["crons"] == [{"path": URL, "schedule": "0 3 * * *"}]
        # Vercel Cron sends a GET, so the path must take one
        methods = {method for route in app.routes if getattr(route, "path", None) == URL for method in route.methods}
        assert "GET" in methods and "POST" in methods
        assert config["routes"] == [{"src": "/(.*)", "dest": "api/index.py"}]  # everything still goes to the app

    def test_the_secret_setting_defaults_to_unset(self, monkeypatch):
        import importlib
        from api.core import config
        monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
        monkeypatch.delenv("CRON_SECRET", raising=False)
        try:
            assert importlib.reload(config).CRON_SECRET == ""
            monkeypatch.setenv("CRON_SECRET", "abc")
            assert importlib.reload(config).CRON_SECRET == "abc"
        finally:
            monkeypatch.undo()
            importlib.reload(config)
