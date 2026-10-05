from fastapi import APIRouter, Depends, HTTPException

from api.queries.projects import get_project_by_id, get_projects_for_user
from api.schemas.project import (
    ProjectDetail,
    ProjectDetailResponse,
    ProjectListItem,
    ProjectListResponse,
)
from api.schemas.auth import UserOut
from api.services.auth import current_user

# Every route needs a signed-in user (any role)
router = APIRouter(prefix="/v1/projects", tags=["Projects"], dependencies=[Depends(current_user)])


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
