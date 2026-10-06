from fastapi import APIRouter, Depends, HTTPException

from api.queries.projects import (
    get_project_by_id,
    get_projects_for_user,
    grant_project_role,
    list_project_roles,
    revoke_project_role,
)
from api.queries.users import get_user_by_uuid
from api.schemas.project import (
    ProjectDetail,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectListResponse,
    ProjectMemberRole,
    ProjectRoleChange,
    ProjectRolesResponse,
)
from api.schemas.auth import UserOut
from api.services.auth import current_admin, current_user, demo_project_fence

# Every route needs a signed-in user (any role); a demo user reaches only their own project
router = APIRouter(
    prefix="/v1/projects", tags=["Projects"], dependencies=[Depends(current_user), Depends(demo_project_fence)]
)


@router.get("/", response_model=ProjectListResponse)
def list_projects_for_user(user: UserOut = Depends(current_user)) -> ProjectListResponse:
    """
    Return every project assigned to the signed-in user.
    Takes the user the request's bearer token belongs to.
    Returns a ProjectListResponse wrapping the list of projects.
    """
    rows = get_projects_for_user(user.uuid)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch projects")

    data = [
        ProjectListItem(
            project_id=row["project_id"],
            project_name=row["project_name"],
            borough=row["borough"],
            status=row["status"],
            user_role=row["user_role"],
            roles=row["roles"],
        )
        for row in rows
    ]

    return ProjectListResponse(
        status="success",
        message=f"Projects for user {user.uuid}",
        data=data,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: str) -> ProjectDetailResponse:
    """
    Return the full detail of a single project.
    Takes the project id as a path parameter.
    Returns a ProjectDetailResponse, or raises 404 if the project does not exist.
    """
    row = get_project_by_id(project_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectDetailResponse(
        status="success",
        message="Project detail",
        data=ProjectDetail(
            project_id=row["project_id"],
            project_name=row["project_name"],
            project_description=row["project_description"],
            registration_code=row["registration_code"],
            borough=row["borough"],
            status=row["status"],
        ),
    )


def _project_roles(project_id: str, message: str) -> ProjectRolesResponse:
    """
    Build the response both roles routes give: who now holds which role on the project.
    Takes the project id and the message to send with the list.
    Returns a ProjectRolesResponse; raises 500 if the list can't be read.
    """
    rows = list_project_roles(project_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to list project roles")

    return ProjectRolesResponse(
        status="success",
        message=message,
        data=[ProjectMemberRole.model_validate(row) for row in rows],
    )


@router.get("/{project_id}/roles", response_model=ProjectRolesResponse, dependencies=[Depends(current_admin)])
def list_roles(project_id: str) -> ProjectRolesResponse:
    """
    List who holds which role on a project, one entry per user and role (demo users left out). Admins only.
    Takes the project id as a path parameter.
    Returns a ProjectRolesResponse; raises 403 for a user who isn't an admin and 404 if the project does not exist.
    """
    if get_project_by_id(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return _project_roles(project_id, "Project roles")


@router.post("/{project_id}/roles", response_model=ProjectRolesResponse, dependencies=[Depends(current_admin)])
def change_role(project_id: str, body: ProjectRoleChange) -> ProjectRolesResponse:
    """
    Give a user a role on a project, or take one away. Admins only. Both are safe to repeat: granting a role already held and revoking one not held succeed and change nothing.
    Takes the project id as a path parameter and a ProjectRoleChange body (user_uuid, role, action).
    Returns a ProjectRolesResponse with the project's roles as they now stand; raises 403 for a user who isn't an admin, 404 for an unknown project or user, and 400 for a demo user.
    """
    if get_project_by_id(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    target = get_user_by_uuid(body.user_uuid)

    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    if target["is_demo"]:
        raise HTTPException(status_code=400, detail="Demo users can't be given project roles")

    change = grant_project_role if body.action == "grant" else revoke_project_role
    rows = change(project_id, body.user_uuid, body.role)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to change the project role")

    if body.action == "grant":
        message = "Role granted" if rows else "Role already held"
    else:
        message = "Role revoked" if rows else "Role was not held"

    return _project_roles(project_id, message)
