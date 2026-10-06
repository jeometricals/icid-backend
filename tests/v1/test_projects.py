from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

import pytest

from tests.conftest import ADMIN_USER_ROW, DEMO_USER_ROW, signed_in

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


# ---------------------------------------------------------------------------
# GET and POST /v1/projects/{project_id}/roles (admins only)
# ---------------------------------------------------------------------------

ROLES_URL = "/v1/projects/HWS0023/roles"
KHAN = UUID("c0000000-0000-4000-8000-000000000003")
OLIVE = UUID("f0000000-0000-4000-8000-000000000006")
ASSIGNED_AT = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)
# A user as api.queries.users.get_user_by_uuid returns them; not an admin
KHAN_ROW = {"uuid": KHAN, "email": "KhanG@magnoleng.pc", "first_name": "Genghis", "last_name": "Khan",
            "client_id": "C00001", "role": None, "is_demo": False}
OLIVE_ROW = {**KHAN_ROW, "uuid": OLIVE, "email": "olive@icid.local", "first_name": "Olive", "last_name": "Engineer"}


def member(user: dict, role: str) -> dict:
    """
    Build a row as list_project_roles returns it.
    Takes the user's row and the role they hold.
    Returns the dict.
    """
    return {"user_uuid": user["uuid"], "email": user["email"], "first_name": user["first_name"],
            "last_name": user["last_name"], "role": role, "assigned_at": ASSIGNED_AT}


MEMBERS = [member(OLIVE_ROW, "oe"), member(KHAN_ROW, "inspector"), member(KHAN_ROW, "re")]


@contextmanager
def roles_backend(project=MOCK_PROJECT_DETAIL_ROW, target=OLIVE_ROW, members=MEMBERS, changed=True):
    """
    Patch the query layer under the roles routes: the project read, the target user's read, the grant or revoke and the list.
    Takes the project row (None: no such project), the user the change names (None: no such user), the list the routes return, and whether the grant or revoke changed a row (None: the statement failed).
    Yields a dict: "changes" is the list of (sql, params) of every grant or revoke run, "lists" the list reads.
    """
    seen = {"changes": [], "lists": []}

    def projects_query(sql, params=None):
        """Stand in for run_query in api.queries.projects."""
        if "INSERT INTO icid.project_users" in sql or "DELETE FROM icid.project_users" in sql:
            seen["changes"].append((" ".join(sql.split()), params))
            return None if changed is None else ([{"project_id": params[0], "user_uuid": params[1], "role": params[2]}]
                                                 if changed else [])
        if "FROM icid.project_users pu" in sql:
            seen["lists"].append((" ".join(sql.split()), params))
            return members
        return [project] if project else []

    with patch("api.queries.projects.run_query", side_effect=projects_query), \
         patch("api.v1.projects.get_user_by_uuid", return_value=target) as user_lookup:
        seen["user"] = user_lookup
        yield seen


def change(user: UUID = OLIVE, role: str = "re", action: str = "grant") -> dict:
    """
    Build a role-change body.
    Takes the user, the role and the action.
    Returns the JSON body.
    """
    return {"user_uuid": str(user), "role": role, "action": action}


class TestListProjectRoles:
    def test_an_admin_gets_one_entry_per_user_and_role(self, admin_client):
        with roles_backend() as seen:
            response = admin_client.get(ROLES_URL)
        assert response.status_code == 200
        data = response.json()["data"]
        assert [(m["email"], m["role"]) for m in data] == [
            ("olive@icid.local", "oe"), ("KhanG@magnoleng.pc", "inspector"), ("KhanG@magnoleng.pc", "re")]
        assert data[0] == {"user_uuid": str(OLIVE), "email": "olive@icid.local", "first_name": "Olive",
                           "last_name": "Engineer", "role": "oe", "assigned_at": "2026-10-06T14:00:00Z"}
        assert seen["changes"] == []

    def test_the_list_is_the_projects_and_leaves_demo_users_out(self, admin_client):
        with roles_backend() as seen:
            admin_client.get(ROLES_URL)
        sql, params = seen["lists"][0]
        assert "FROM icid.project_users pu JOIN icid.users u ON u.uuid = pu.user_uuid" in sql
        assert "WHERE pu.project_id = %s AND u.is_demo = false" in sql and params == ("HWS0023",)
        assert sql.endswith("ORDER BY u.last_name NULLS LAST, u.first_name NULLS LAST, u.email, pu.role;")

    def test_a_project_with_nobody_on_it_is_an_empty_list(self, admin_client):
        with roles_backend(members=[]):
            response = admin_client.get(ROLES_URL)
        assert response.status_code == 200 and response.json()["data"] == []

    def test_an_unknown_project_is_404(self, admin_client):
        with roles_backend(project=None) as seen:
            response = admin_client.get(ROLES_URL)
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"}
        assert seen["lists"] == []

    def test_a_failed_read_is_500(self, admin_client):
        with roles_backend(members=None):
            assert admin_client.get(ROLES_URL).status_code == 500


class TestChangeProjectRole:
    def test_granting_adds_the_row_and_returns_the_roles_as_they_now_stand(self, admin_client):
        after = MEMBERS + [member(OLIVE_ROW, "re")]
        with roles_backend(members=after) as seen:
            response = admin_client.post(ROLES_URL, json=change(OLIVE, "re", "grant"))
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "Role granted" and len(body["data"]) == 4
        sql, params = seen["changes"][0]
        assert sql.startswith("INSERT INTO icid.project_users (project_id, user_uuid, role) VALUES (%s, %s, %s)")
        assert "ON CONFLICT (project_id, user_uuid, role) DO NOTHING" in sql
        assert params == ("HWS0023", OLIVE, "re")
        seen["user"].assert_called_once_with(OLIVE)

    def test_granting_a_role_already_held_succeeds_and_changes_nothing(self, admin_client):
        with roles_backend(changed=False) as seen:
            response = admin_client.post(ROLES_URL, json=change(OLIVE, "oe", "grant"))
        assert response.status_code == 200 and response.json()["message"] == "Role already held"
        assert len(seen["changes"]) == 1 and len(response.json()["data"]) == 3

    def test_revoking_removes_only_that_role(self, admin_client):
        after = [member(OLIVE_ROW, "oe"), member(KHAN_ROW, "inspector")]
        with roles_backend(target=KHAN_ROW, members=after) as seen:
            response = admin_client.post(ROLES_URL, json=change(KHAN, "re", "revoke"))
        assert response.status_code == 200 and response.json()["message"] == "Role revoked"
        sql, params = seen["changes"][0]
        assert sql.startswith("DELETE FROM icid.project_users WHERE project_id = %s AND user_uuid = %s AND role = %s")
        assert params == ("HWS0023", KHAN, "re")
        assert [(m["email"], m["role"]) for m in response.json()["data"]] == [
            ("olive@icid.local", "oe"), ("KhanG@magnoleng.pc", "inspector")]

    def test_revoking_a_role_not_held_succeeds_and_changes_nothing(self, admin_client):
        with roles_backend(changed=False):
            response = admin_client.post(ROLES_URL, json=change(OLIVE, "re", "revoke"))
        assert response.status_code == 200 and response.json()["message"] == "Role was not held"

    @pytest.mark.parametrize("role", ["inspector", "oe", "re"])
    def test_each_project_role_can_be_granted(self, admin_client, role):
        with roles_backend() as seen:
            assert admin_client.post(ROLES_URL, json=change(role=role)).status_code == 200
        assert seen["changes"][0][1][2] == role

    @pytest.mark.parametrize("body", [
        {}, {"user_uuid": str(OLIVE), "role": "re"}, change(role="admin"), change(role="RE"), change(action="delete"),
        {**change(), "user_uuid": "not-a-uuid"}, {"role": "re", "action": "grant"},
    ])
    def test_a_malformed_body_is_422_and_changes_nothing(self, admin_client, body):
        with roles_backend() as seen:
            assert admin_client.post(ROLES_URL, json=body).status_code == 422
        assert seen["changes"] == []

    def test_an_unknown_project_is_404(self, admin_client):
        with roles_backend(project=None) as seen:
            response = admin_client.post(ROLES_URL, json=change())
        assert response.status_code == 404 and response.json() == {"detail": "Project not found"}
        assert seen["changes"] == []

    def test_an_unknown_user_is_404(self, admin_client):
        with roles_backend(target=None) as seen:
            response = admin_client.post(ROLES_URL, json=change())
        assert response.status_code == 404 and response.json() == {"detail": "User not found"}
        assert seen["changes"] == []

    def test_a_demo_user_cant_be_given_a_role(self, admin_client):
        with roles_backend(target=DEMO_USER_ROW) as seen:
            response = admin_client.post(ROLES_URL, json=change(DEMO_USER_ROW["uuid"]))
        assert response.status_code == 400
        assert response.json() == {"detail": "Demo users can't be given project roles"}
        assert seen["changes"] == []

    def test_a_failed_statement_is_500(self, admin_client):
        with roles_backend(changed=None):
            assert admin_client.post(ROLES_URL, json=change()).status_code == 500


class TestProjectRolesAreAdminOnly:
    @pytest.mark.parametrize("method,body", [("GET", None), ("POST", change())])
    def test_a_user_who_isnt_an_admin_is_403(self, method, body):
        # even one who is an RE on the project
        with signed_in(OLIVE_ROW) as client, roles_backend() as seen:
            response = client.request(method, ROLES_URL, json=body)
        assert response.status_code == 403 and response.json() == {"detail": "Admin access required"}
        assert seen["changes"] == [] and seen["lists"] == []

    @pytest.mark.parametrize("method,body", [("GET", None), ("POST", change())])
    def test_a_demo_user_gets_nowhere(self, demo_client, method, body):
        with roles_backend(project={**MOCK_PROJECT_DETAIL_ROW, "project_id": "DEMO01"}) as seen:
            response = demo_client.request(method, "/v1/projects/DEMO01/roles", json=body)
        assert response.status_code in (403, 404)
        assert seen["changes"] == [] and seen["lists"] == []

    def test_an_admin_needs_no_assignment_to_the_project(self, admin_client):
        with roles_backend(members=[]):
            assert admin_client.get(ROLES_URL).status_code == 200
