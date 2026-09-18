from unittest.mock import patch
from uuid import UUID

# ---------------------------------------------------------------------------
# Mock data — matches actual icid.users schema (uuid, client_id aliased as employer)
# ---------------------------------------------------------------------------

MOCK_USER_ROWS = [
    (UUID("7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90"), "KhanG@magnoleng.pc", "Genghis", "Khan", "(914) 345-6789", "C00046"),
    (UUID("0d9e8f7a-6b5c-4d3e-8f1a-2b3c4d5e6f70"), "Nadir.shah@goorkaneng.com", "Nadir", "Shah", "(201) 987-6543", "C00045"),
]


# ---------------------------------------------------------------------------
# GET /v1/users/
# ---------------------------------------------------------------------------

class TestListAllUsers:
    def test_returns_200(self, client):
        with patch("api.queries.users.run_query", return_value=MOCK_USER_ROWS):
            response = client.get("/v1/users/")
        assert response.status_code == 200

    def test_response_shape(self, client):
        with patch("api.queries.users.run_query", return_value=MOCK_USER_ROWS):
            data = client.get("/v1/users/").json()
        assert data["status"] == "success"
        assert "message" in data
        assert isinstance(data["data"], list)

    def test_user_fields_present(self, client):
        with patch("api.queries.users.run_query", return_value=MOCK_USER_ROWS):
            users = client.get("/v1/users/").json()["data"]
        for user in users:
            assert "user_id" in user
            assert "email" in user
            assert "first_name" in user
            assert "last_name" in user
            assert "phone_number" in user
            assert "employer" in user

    def test_returns_correct_count(self, client):
        with patch("api.queries.users.run_query", return_value=MOCK_USER_ROWS):
            users = client.get("/v1/users/").json()["data"]
        assert len(users) == 2

    def test_user_id_is_string(self, client):
        with patch("api.queries.users.run_query", return_value=MOCK_USER_ROWS):
            users = client.get("/v1/users/").json()["data"]
        assert isinstance(users[0]["user_id"], str)
        assert users[0]["user_id"] == "7f3c2a9e-1b4d-4c8a-9e2f-3a5b6c7d8e90"

    def test_employer_is_client_id(self, client):
        with patch("api.queries.users.run_query", return_value=MOCK_USER_ROWS):
            users = client.get("/v1/users/").json()["data"]
        assert users[0]["employer"] == "C00046"

    def test_empty_list(self, client):
        with patch("api.queries.users.run_query", return_value=[]):
            data = client.get("/v1/users/").json()
        assert data["status"] == "success"
        assert data["data"] == []

    def test_db_failure_returns_500(self, client):
        with patch("api.queries.users.run_query", return_value=None):
            response = client.get("/v1/users/")
        assert response.status_code == 500
