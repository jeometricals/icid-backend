from unittest.mock import patch

from tests.conftest import ADMIN_USER_ROW

# ---------------------------------------------------------------------------
# Mock data — dict rows, as run_query returns them under dict_row.
# Keys match the SELECT column names in api/queries/projects.py.
# project_users.user_uuid references users.uuid (Postgres UUID)
# ---------------------------------------------------------------------------

MOCK_PROJECT_ROWS = [
    {
        "project_id": "P001",
        "project_name": "Brooklyn Bridge Rehab",
        "borough": "Brooklyn",
        "status": "active",
        "user_role": "inspector",
        "roles": ["inspector"],
    },
    {
        "project_id": "P002",
        "project_name": "Queens Plaza Upgrade",
        "borough": "Queens",
        "status": "active",
        "user_role": "supervisor",
        "roles": ["inspector", "oe", "re"],
    },
    {
        "project_id": "P003",
        "project_name": "Bronx Transit Hub",
        "borough": "Bronx",
        "status": "pending",
        "user_role": None,
        "roles": ["re"],
    },
]

MOCK_PROJECT_DETAIL_ROW = {
    "project_id": "P001",
    "project_name": "Brooklyn Bridge Rehab",
    "project_description": "Full rehabilitation of the Brooklyn Bridge deck.",
    "registration_code": "REG-2024-001",
    "borough": "Brooklyn",
    "status": "active",
}



# ---------------------------------------------------------------------------
# GET /v1/projects/ (the signed-in user's projects)
# ---------------------------------------------------------------------------

class TestListProjectsForUser:
    def test_returns_200(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            response = admin_client.get("/v1/projects/")
        assert response.status_code == 200

    def test_response_shape(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            data = admin_client.get("/v1/projects/").json()
        assert data["status"] == "success"
        assert "message" in data
        assert isinstance(data["data"], list)

    def test_project_fields_present(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            projects = admin_client.get("/v1/projects/").json()["data"]
        for p in projects:
            assert "project_id" in p
            assert "project_name" in p
            assert "borough" in p
            assert "status" in p
            assert "user_role" in p

    def test_each_project_carries_the_users_roles_on_it(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            projects = admin_client.get("/v1/projects/").json()["data"]
        assert [p["roles"] for p in projects] == [["inspector"], ["inspector", "oe", "re"], ["re"]]
        assert [p["user_role"] for p in projects] == ["inspector", "supervisor", None]  # the label, unchanged

    def test_a_project_is_listed_once_however_many_roles_the_user_holds(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS) as run:
            admin_client.get("/v1/projects/")
        sql = " ".join(run.call_args.args[0].split())
        assert "array_agg(pu.role ORDER BY pu.role) AS roles" in sql and "GROUP BY p.project_id" in sql
        assert "(array_agg(pu.user_role ORDER BY pu.assigned_at) FILTER (WHERE pu.user_role IS NOT NULL))[1]" in sql

    def test_returns_correct_count(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            projects = admin_client.get("/v1/projects/").json()["data"]
        assert len(projects) == 3

    def test_empty_list_when_no_projects(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=[]):
            data = admin_client.get("/v1/projects/").json()
        assert data["status"] == "success"
        assert data["data"] == []

    def test_lists_the_signed_in_users_projects(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS) as run:
            data = admin_client.get("/v1/projects/").json()
        sql, params = run.call_args.args
        assert "icid.project_users" in sql and params == (ADMIN_USER_ROW["uuid"],)
        assert data["message"] == f"Projects for user {ADMIN_USER_ROW['uuid']}"

    def test_a_user_id_query_parameter_is_ignored(self, admin_client):
        # the old ?user_id= no longer chooses whose projects are listed, whatever it holds
        for other in ("7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90", "not-a-uuid"):
            with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS) as run:
                response = admin_client.get(f"/v1/projects/?user_id={other}")
            assert response.status_code == 200
            assert run.call_args.args[1] == (ADMIN_USER_ROW["uuid"],)

    def test_an_admin_sees_only_assigned_projects(self, admin_client):
        # no admin bypass: the admin role doesn't widen the list beyond project_users
        with patch("api.queries.projects.run_query", return_value=[]) as run:
            data = admin_client.get("/v1/projects/").json()
        assert data["data"] == [] and run.call_count == 1

    def test_db_failure_returns_500(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=None):
            response = admin_client.get("/v1/projects/")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}
# ---------------------------------------------------------------------------

class TestGetProjectDetail:
    def test_returns_200(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=[MOCK_PROJECT_DETAIL_ROW]):
            response = admin_client.get("/v1/projects/P001")
        assert response.status_code == 200

    def test_response_shape(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=[MOCK_PROJECT_DETAIL_ROW]):
            data = admin_client.get("/v1/projects/P001").json()
        assert data["status"] == "success"
        assert "data" in data

    def test_project_detail_fields(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=[MOCK_PROJECT_DETAIL_ROW]):
            project = admin_client.get("/v1/projects/P001").json()["data"]
        assert project["project_id"] == "P001"
        assert project["project_name"] == "Brooklyn Bridge Rehab"
        assert project["borough"] == "Brooklyn"
        assert project["status"] == "active"
        assert project["registration_code"] == "REG-2024-001"
        assert "project_description" in project

    def test_not_found_returns_404(self, admin_client):
        with patch("api.queries.projects.run_query", return_value=[]):
            response = admin_client.get("/v1/projects/DOESNOTEXIST")
        assert response.status_code == 404

    def test_optional_fields_can_be_null(self, admin_client):
        row_with_nulls = {
            "project_id": "P002",
            "project_name": "Queens Plaza Upgrade",
            "project_description": None,
            "registration_code": None,
            "borough": "Queens",
            "status": "active",
        }
        with patch("api.queries.projects.run_query", return_value=[row_with_nulls]):
            project = admin_client.get("/v1/projects/P002").json()["data"]
        assert project["project_description"] is None
        assert project["registration_code"] is None
