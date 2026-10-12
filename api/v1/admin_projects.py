import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.projects import create_project, list_projects_for_admin
from api.schemas.project import (
    AdminProjectCreate,
    AdminProjectListResponse,
    AdminProjectResponse,
    ProjectConflict,
    ProjectDetail,
)
from api.services.auth import current_admin

# Every route needs a signed-in admin. Its own router: /v1/admin also lets the scheduler's secret in
router = APIRouter(prefix="/v1/admin/projects", tags=["Admin projects"], dependencies=[Depends(current_admin)])

# The longest value each field takes
MAX_PROJECT_ID_LENGTH = 32
MAX_PROJECT_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 200
MAX_BOROUGH_LENGTH = 50
MAX_REGISTRATION_CODE_LENGTH = 50
# A project id is part of page URLs and Storage paths
_PROJECT_ID = re.compile(r"[A-Za-z0-9_-]+")


def _clean_text(value: Optional[str], field: str, max_length: int, required: bool = True) -> Optional[str]:
    """
    Read one text field off the request: trimmed, and refused when too long, or blank where it is required.
    Takes the value as sent, the field's name (for the error), its longest length and whether it must be given.
    Returns the trimmed text, or None for an optional field left blank; raises 400 otherwise.
    """
    text = (value or "").strip()
    if not text:
        if required:
            raise HTTPException(status_code=400, detail=f"{field} cannot be blank")
        return None
    if len(text) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} cannot be longer than {max_length} characters")
    return text


@router.get("", response_model=AdminProjectListResponse)
def list_projects() -> AdminProjectListResponse:
    """
    List every project, whoever is assigned to it, by project id. Admins only.
    Takes nothing.
    Returns an AdminProjectListResponse; raises 403 for a user who isn't an admin and 500 if the list can't be read.
    """
    rows = list_projects_for_admin()

    if rows is None:
        raise HTTPException(status_code=500, detail="Failed to fetch projects")

    return AdminProjectListResponse(data=[ProjectDetail.model_validate(row) for row in rows])


@router.post("", response_model=AdminProjectResponse, status_code=201, responses={409: {"model": ProjectConflict}})
def create_project_record(body: AdminProjectCreate) -> AdminProjectResponse | JSONResponse:
    """
    Create a project. Admins only. It has no status, no clients and nobody assigned: it is in no one's project list until an admin gives someone a role on it.
    Takes the project id (letters, digits, hyphens and underscores), name, description, borough and optional registration code (blank is none) in the body, each trimmed.
    Returns 201 with an AdminProjectResponse; raises 400 for a value that doesn't fit, 403 for a user who isn't an admin, and 409 (with existing_project_id) when the id is taken.
    """
    project_id = _clean_text(body.project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    if not _PROJECT_ID.fullmatch(project_id):
        raise HTTPException(
            status_code=400, detail="project_id can only contain letters, digits, hyphens and underscores"
        )
    project_name = _clean_text(body.project_name, "project_name", MAX_PROJECT_NAME_LENGTH)
    project_description = _clean_text(body.project_description, "project_description", MAX_DESCRIPTION_LENGTH)
    borough = _clean_text(body.borough, "borough", MAX_BOROUGH_LENGTH)
    registration_code = _clean_text(
        body.registration_code, "registration_code", MAX_REGISTRATION_CODE_LENGTH, required=False
    )

    row = create_project(project_id, project_name, project_description, borough, registration_code)

    if row is None:
        conflict = ProjectConflict(detail="A project with this project_id already exists", existing_project_id=project_id)
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return AdminProjectResponse(data=ProjectDetail.model_validate(row))
