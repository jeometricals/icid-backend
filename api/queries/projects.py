from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query


def get_projects_for_user(user_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every project assigned to a user, once each, with the roles they hold on it and their label there.
    Takes the user uuid.
    Returns a list of project dicts ordered by project name (roles is a sorted list; user_role is the label of their earliest assignment that has one), or None on failure.
    """
    sql = """
        SELECT
            p.project_id,
            p.project_name,
            p.borough,
            p.status,
            (array_agg(pu.user_role ORDER BY pu.assigned_at) FILTER (WHERE pu.user_role IS NOT NULL))[1] AS user_role,
            array_agg(pu.role ORDER BY pu.role) AS roles
        FROM icid.projects p
        JOIN icid.project_users pu ON p.project_id = pu.project_id
        WHERE pu.user_uuid = %s
        GROUP BY p.project_id
        ORDER BY p.project_name;
    """
    return run_query(sql, (user_id,))


def is_user_on_project(user_id: UUID, project_id: str) -> bool:
    """
    Check whether a user is assigned to a project.
    Takes the user uuid and the project id.
    Returns True if an assignment row exists, False otherwise.
    """
    sql = """
        SELECT 1
        FROM icid.project_users
        WHERE user_uuid = %s AND project_id = %s
        LIMIT 1;
    """
    rows = run_query(sql, (user_id, project_id))
    return bool(rows)


def get_user_roles_on_project(user_id: UUID, project_id: str) -> set[str]:
    """
    Fetch the roles a user holds on a project.
    Takes the user uuid and the project id.
    Returns the set of roles ('inspector', 'oe', 're'), empty when they hold none or the lookup fails.
    """
    sql = """
        SELECT role
        FROM icid.project_users
        WHERE user_uuid = %s AND project_id = %s;
    """
    rows = run_query(sql, (user_id, project_id))
    return {row["role"] for row in rows or []}


def get_project_contractor_name(project_id: str) -> Optional[str]:
    """
    Fetch the name of the organisation on a project in the Contractor role.
    Takes the project id.
    Returns the contractor's client_name (the first by name if several), or None when the project has none.
    """
    sql = """
        SELECT c.client_name
        FROM icid.project_clients pc
        JOIN icid.clients c ON c.client_id = pc.client_id
        WHERE pc.project_id = %s AND pc.client_role = 'Contractor'
        ORDER BY c.client_name
        LIMIT 1;
    """
    rows = run_query(sql, (project_id,))
    return rows[0]["client_name"] if rows else None


def get_project_by_id(project_id: str) -> Optional[dict[str, Any]]:
    """
    Fetch the full detail of a single project.
    Takes the project id.
    Returns the project dict, or None if no project matches.
    """
    sql = """
        SELECT
            project_id,
            project_name,
            project_description,
            registration_code,
            borough,
            status
        FROM icid.projects
        WHERE project_id = %s;
    """
    rows = run_query(sql, (project_id,))
    return rows[0] if rows else None
