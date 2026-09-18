from uuid import UUID

from fastapi import APIRouter, HTTPException
from api.schemas.project import (
    ProjectListItem,
    ProjectListResponse,
    ProjectDetail,
    ProjectDetailResponse,
)
from api.queries.projects import get_projects_for_user, get_project_by_id

router = APIRouter(prefix="/v1/projects", tags=["Projects"])


@router.get("/", response_model=ProjectListResponse)
def list_projects_for_user(user_id: UUID):
    """
    Return all projects assigned to the given user.
    Query param: ?user_id=<uuid>
    """
    rows = get_projects_for_user(user_id)

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch projects")

    data = [
        ProjectListItem(
            project_id=row[0],
            project_name=row[1],
            borough=row[2],
            status=row[3],
            user_role=row[4],
        )
        for row in rows
    ]

    return ProjectListResponse(
        status="success",
        message=f"Projects for user {user_id}",
        data=data,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: str):
    """Return full detail for a single project."""
    row = get_project_by_id(project_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return ProjectDetailResponse(
        status="success",
        message="Project detail",
        data=ProjectDetail(
            project_id=row[0],
            project_name=row[1],
            project_description=row[2],
            registration_code=row[3],
            borough=row[4],
            status=row[5],
        ),
    )
