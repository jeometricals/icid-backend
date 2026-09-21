from typing import Any, Optional
from uuid import UUID

from api.db.runner import run_query


def get_projects_for_user(user_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    Fetch every project assigned to a user, including their role on it.
    Takes the user uuid.
    Returns a list of project dicts ordered by project name, or None on failure.
    """
    sql = """
        SELECT
            p.project_id,
            p.project_name,
            p.borough,
            p.status,
            pu.user_role
        FROM icid.projects p
        JOIN icid.project_users pu ON p.project_id = pu.project_id
        WHERE pu.user_uuid = %s
        ORDER BY p.project_name;
    """
    return run_query(sql, (user_id,))


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
