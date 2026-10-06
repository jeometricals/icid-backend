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


def list_project_roles(project_id: str) -> Optional[list[dict[str, Any]]]:
    """
    List who holds which role on a project, one row per user and role, demo users left out.
    Takes the project id.
    Returns a list of dicts (user_uuid, email, first_name, last_name, role, assigned_at) ordered by name then role, or None on failure.
    """
    sql = """
        SELECT
            pu.user_uuid,
            u.email,
            u.first_name,
            u.last_name,
            pu.role,
            pu.assigned_at
        FROM icid.project_users pu
        JOIN icid.users u ON u.uuid = pu.user_uuid
        WHERE pu.project_id = %s AND u.is_demo = false
        ORDER BY u.last_name NULLS LAST, u.first_name NULLS LAST, u.email, pu.role;
    """
    return run_query(sql, (project_id,))


def grant_project_role(project_id: str, user_id: UUID, role: str) -> Optional[list[dict[str, Any]]]:
    """
    Give a user a role on a project, unless they already hold it.
    Takes the project id, the user uuid and the role ('inspector', 'oe' or 're').
    Returns a one-row list with the new assignment, an empty list if they already held the role, or None on failure.
    """
    sql = """
        INSERT INTO icid.project_users (project_id, user_uuid, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (project_id, user_uuid, role) DO NOTHING
        RETURNING project_id, user_uuid, role;
    """
    return run_query(sql, (project_id, user_id, role))


def revoke_project_role(project_id: str, user_id: UUID, role: str) -> Optional[list[dict[str, Any]]]:
    """
    Take a role on a project away from a user. Their other roles there are untouched; without any they are off the project.
    Takes the project id, the user uuid and the role.
    Returns a one-row list with the removed assignment, an empty list if they didn't hold the role, or None on failure.
    """
    sql = """
        DELETE FROM icid.project_users
        WHERE project_id = %s AND user_uuid = %s AND role = %s
        RETURNING project_id, user_uuid, role;
    """
    return run_query(sql, (project_id, user_id, role))


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
