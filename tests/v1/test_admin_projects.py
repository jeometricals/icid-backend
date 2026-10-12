from unittest.mock import patch

import pytest

from api.queries.projects import create_project, list_projects_for_admin
from api.services import auth
from tests.conftest import ADMIN_USER_ROW, signed_in

# ---------------------------------------------------------------------------
# GET and POST /v1/admin/projects
# Mock rows are dicts, as run_query returns them: a project's own columns.
# ---------------------------------------------------------------------------

URL = "/v1/admin/projects"
QUERY = "api.queries.projects.run_query"
CRON_SECRET = "cron-secret-for-tests-only"
CRON = {"Authorization": f"Bearer {CRON_SECRET}"}

HWS_ROW = {"project_id": "HWS0023", "project_name": "S/W Queens 2025",
           "project_description": "Installation of Curb, Sidewalk and Ped-Ramp Various locations Queens",
           "registration_code": "2024123457", "borough": "Queens", "status": "Construction; Active"}
DEMO_ROW = {"project_id": "DEMO01", "project_name": "Test Project", "project_description": None,
            "registration_code": None, "borough": None, "status": None}
NEW_ROW = {"project_id": "HWK2026", "project_name": "S/W Brooklyn 2026",
           "project_description": "Installation of Sidewalk in Brooklyn", "registration_code": "2026000001",
           "borough": "Brooklyn", "status": None}
FULL_BODY = {"project_id": "HWK2026", "project_name": "S/W Brooklyn 2026",
             "project_description": "Installation of Sidewalk in Brooklyn", "borough": "Brooklyn",
             "registration_code": "2026000001"}
MINIMUM_BODY = {key: value for key, value in FULL_BODY.items() if key != "registration_code"}
# A regular user: signed in, no admin role
INSPECTOR_ROW = {**ADMIN_USER_ROW, "role": None}
FORBIDDEN = (403, {"detail": "Admin access required"})


@pytest.fixture
def cron_secret(monkeypatch):
    """
    Give the app a CRON_SECRET for one test.
    """
    monkeypatch.setattr(auth, "CRON_SECRET", CRON_SECRET)


class TestListAdminProjects:
    def test_an_admin_gets_every_project(self, admin_client):
        with patch(QUERY, return_value=[DEMO_ROW, HWS_ROW]) as run:
            response = admin_client.get(URL)
        assert response.status_code == 200 and response.json() == {"data": [DEMO_ROW, HWS_ROW]}
        assert run.call_count == 1

    def test_the_statement_reads_every_project_by_id(self, admin_client):
        with patch(QUERY, return_value=[]) as run:
            assert admin_client.get(URL).json() == {"data": []}
        sql = run.call_args.args[0]
        assert "FROM icid.projects" in sql and "ORDER BY project_id;" in sql
        assert "WHERE" not in sql and "project_users" not in sql

    def test_a_user_who_isnt_an_admin_is_forbidden(self):
        with signed_in(INSPECTOR_ROW) as inspector, patch(QUERY) as run:
            response = inspector.get(URL)
        assert (response.status_code, response.json()) == FORBIDDEN
        run.assert_not_called()

    def test_a_demo_user_is_forbidden(self, demo_client):
        with patch(QUERY) as run:
            assert demo_client.get(URL).status_code == 403
        run.assert_not_called()

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(QUERY) as run:
            assert client.get(URL, headers=CRON).status_code == 401  # the secret is no user's token
        run.assert_not_called()

    def test_a_failed_read_is_500(self, admin_client):
        with patch(QUERY, return_value=None):
            response = admin_client.get(URL)
        assert response.status_code == 500 and response.json() == {"detail": "Failed to fetch projects"}


class TestCreateAdminProject:
    def test_a_full_body_creates_the_project(self, admin_client):
        with patch(QUERY, return_value=[NEW_ROW]) as run:
            response = admin_client.post(URL, json=FULL_BODY)
        assert response.status_code == 201 and response.json() == {"data": NEW_ROW}
        assert run.call_count == 1
        assert run.call_args.args[1] == ("HWK2026", "S/W Brooklyn 2026", "Installation of Sidewalk in Brooklyn",
                                         "Brooklyn", "2026000001")

    @pytest.mark.parametrize("body", [MINIMUM_BODY, {**FULL_BODY, "registration_code": ""},
                                      {**FULL_BODY, "registration_code": "   "},
                                      {**FULL_BODY, "registration_code": None}])
    def test_no_registration_code_is_stored_as_none(self, admin_client, body):
        with patch(QUERY, return_value=[{**NEW_ROW, "registration_code": None}]) as run:
            response = admin_client.post(URL, json=body)
        assert response.status_code == 201 and response.json()["data"]["registration_code"] is None
        assert run.call_args.args[1][4] is None

    def test_every_value_is_trimmed(self, admin_client):
        body = {key: f"  {value}\t" for key, value in FULL_BODY.items()}
        with patch(QUERY, return_value=[NEW_ROW]) as run:
            assert admin_client.post(URL, json=body).status_code == 201
        assert run.call_args.args[1] == ("HWK2026", "S/W Brooklyn 2026", "Installation of Sidewalk in Brooklyn",
                                         "Brooklyn", "2026000001")

    def test_the_statement_inserts_into_icid_projects_and_skips_a_taken_id(self, admin_client):
        with patch(QUERY, return_value=[NEW_ROW]) as run:
            admin_client.post(URL, json=FULL_BODY)
        sql = run.call_args.args[0]
        assert ("INSERT INTO icid.projects (project_id, project_name, project_description, borough, "
                "registration_code)") in sql
        assert "VALUES (%s, %s, %s, %s, %s)" in sql
        assert "ON CONFLICT (project_id) DO NOTHING" in sql and "RETURNING" in sql

    def test_a_taken_project_id_is_409_naming_it(self, admin_client):
        with patch(QUERY, return_value=[]) as run:
            response = admin_client.post(URL, json={**FULL_BODY, "project_id": "HWS0023"})
        assert response.status_code == 409
        assert response.json() == {"detail": "A project with this project_id already exists",
                                   "existing_project_id": "HWS0023"}
        assert run.call_count == 1

    @pytest.mark.parametrize("change,detail", [
        ({"project_id": ""}, "project_id cannot be blank"),
        ({"project_id": "   "}, "project_id cannot be blank"),
        ({"project_id": "P" * 33}, "project_id cannot be longer than 32 characters"),
        ({"project_id": "HWK 2026"}, "project_id can only contain letters, digits, hyphens and underscores"),
        ({"project_id": "HWK/2026"}, "project_id can only contain letters, digits, hyphens and underscores"),
        ({"project_id": "HWK2026?"}, "project_id can only contain letters, digits, hyphens and underscores"),
        ({"project_id": "HWK2026\nX"}, "project_id can only contain letters, digits, hyphens and underscores"),
        ({"project_name": ""}, "project_name cannot be blank"),
        ({"project_name": "n" * 101}, "project_name cannot be longer than 100 characters"),
        ({"project_description": ""}, "project_description cannot be blank"),
        ({"project_description": " \t"}, "project_description cannot be blank"),
        ({"project_description": "d" * 201}, "project_description cannot be longer than 200 characters"),
        ({"borough": ""}, "borough cannot be blank"),
        ({"borough": "b" * 51}, "borough cannot be longer than 50 characters"),
        ({"registration_code": "r" * 51}, "registration_code cannot be longer than 50 characters"),
    ])
    def test_a_value_that_doesnt_fit_is_400_and_nothing_is_written(self, admin_client, change, detail):
        with patch(QUERY) as run:
            response = admin_client.post(URL, json={**FULL_BODY, **change})
        assert response.status_code == 400 and response.json() == {"detail": detail}
        run.assert_not_called()

    def test_values_at_the_limits_are_taken(self, admin_client):
        body = {"project_id": "A-b_9" + "P" * 27, "project_name": "n" * 100, "project_description": "d" * 200,
                "borough": "b" * 50, "registration_code": "r" * 50}
        with patch(QUERY, return_value=[NEW_ROW]) as run:
            assert admin_client.post(URL, json=body).status_code == 201
        assert run.call_args.args[1][0] == body["project_id"]

    @pytest.mark.parametrize("missing", ["project_id", "project_name", "project_description", "borough"])
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

    def test_the_schedulers_secret_doesnt_open_it(self, client, cron_secret):
        with patch(QUERY) as run:
            assert client.post(URL, json=FULL_BODY, headers=CRON).status_code == 401
        run.assert_not_called()


class TestAdminProjectQueries:
    def test_the_list_is_the_rows_or_none(self):
        with patch(QUERY, return_value=[HWS_ROW]):
            assert list_projects_for_admin() == [HWS_ROW]
        with patch(QUERY, return_value=None):
            assert list_projects_for_admin() is None

    def test_create_returns_the_row_or_none_when_the_id_is_taken(self):
        with patch(QUERY, return_value=[NEW_ROW]):
            assert create_project("HWK2026", "S/W Brooklyn 2026", "Sidewalk", "Brooklyn", None) == NEW_ROW
        with patch(QUERY, return_value=[]):
            assert create_project("HWK2026", "S/W Brooklyn 2026", "Sidewalk", "Brooklyn", None) is None
