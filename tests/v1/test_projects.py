from unittest.mock import patch

# ---------------------------------------------------------------------------
# Mock data — matches actual icid schema
# project_users.user_uuid references users.uuid (Postgres UUID)
# ---------------------------------------------------------------------------

MOCK_PROJECT_ROWS = [
    ("P001", "Brooklyn Bridge Rehab", "Brooklyn", "active", "inspector"),
    ("P002", "Queens Plaza Upgrade", "Queens", "active", "supervisor"),
    ("P003", "Bronx Transit Hub", "Bronx", "pending", "inspector"),
]

MOCK_PROJECT_DETAIL_ROW = (
    "P001",
    "Brooklyn Bridge Rehab",
    "Full rehabilitation of the Brooklyn Bridge deck.",
    "REG-2024-001",
    "Brooklyn",
    "active",
)

DEV_USER_ID = "7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90"  # users.uuid


# ---------------------------------------------------------------------------
# GET /v1/projects/?user_id=<uuid>
# ---------------------------------------------------------------------------

class TestListProjectsForUser:
    def test_returns_200(self, client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            response = client.get(f"/v1/projects/?user_id={DEV_USER_ID}")
        assert response.status_code == 200

    def test_response_shape(self, client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            data = client.get(f"/v1/projects/?user_id={DEV_USER_ID}").json()
        assert data["status"] == "success"
        assert "message" in data
        assert isinstance(data["data"], list)

    def test_project_fields_present(self, client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            projects = client.get(f"/v1/projects/?user_id={DEV_USER_ID}").json()["data"]
        for p in projects:
            assert "project_id" in p
            assert "project_name" in p
            assert "borough" in p
            assert "status" in p
            assert "user_role" in p

    def test_returns_correct_count(self, client):
        with patch("api.queries.projects.run_query", return_value=MOCK_PROJECT_ROWS):
            projects = client.get(f"/v1/projects/?user_id={DEV_USER_ID}").json()["data"]
        assert len(projects) == 3

    def test_empty_list_when_no_projects(self, client):
        with patch("api.queries.projects.run_query", return_value=[]):
            data = client.get(f"/v1/projects/?user_id={DEV_USER_ID}").json()
        assert data["status"] == "success"
        assert data["data"] == []

    def test_missing_user_id_returns_422(self, client):
        response = client.get("/v1/projects/")
        assert response.status_code == 422

    def test_non_uuid_user_id_returns_422(self, client):
        response = client.get("/v1/projects/?user_id=not-a-uuid")
        assert response.status_code == 422

    def test_integer_user_id_returns_422(self, client):
        response = client.get("/v1/projects/?user_id=28")
        assert response.status_code == 422

    def test_db_failure_returns_500(self, client):
        with patch("api.queries.projects.run_query", return_value=None):
            response = client.get(f"/v1/projects/?user_id={DEV_USER_ID}")
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}
# ---------------------------------------------------------------------------

class TestGetProjectDetail:
    def test_returns_200(self, client):
        with patch("api.queries.projects.run_query", return_value=[MOCK_PROJECT_DETAIL_ROW]):
            response = client.get("/v1/projects/P001")
        assert response.status_code == 200

    def test_response_shape(self, client):
        with patch("api.queries.projects.run_query", return_value=[MOCK_PROJECT_DETAIL_ROW]):
            data = client.get("/v1/projects/P001").json()
        assert data["status"] == "success"
        assert "data" in data

    def test_project_detail_fields(self, client):
        with patch("api.queries.projects.run_query", return_value=[MOCK_PROJECT_DETAIL_ROW]):
            project = client.get("/v1/projects/P001").json()["data"]
        assert project["project_id"] == "P001"
        assert project["project_name"] == "Brooklyn Bridge Rehab"
        assert project["borough"] == "Brooklyn"
        assert project["status"] == "active"
        assert project["registration_code"] == "REG-2024-001"
        assert "project_description" in project

    def test_not_found_returns_404(self, client):
        with patch("api.queries.projects.run_query", return_value=[]):
            response = client.get("/v1/projects/DOESNOTEXIST")
        assert response.status_code == 404

    def test_optional_fields_can_be_null(self, client):
        row_with_nulls = ("P002", "Queens Plaza Upgrade", None, None, "Queens", "active")
        with patch("api.queries.projects.run_query", return_value=[row_with_nulls]):
            project = client.get("/v1/projects/P002").json()["data"]
        assert project["project_description"] is None
        assert project["registration_code"] is None
