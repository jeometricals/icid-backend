import re
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.queries.projects import create_project, list_projects_for_admin, update_assessment_rates
from api.schemas.project import (
    AdminProjectCreate,
    AdminProjectListResponse,
    AdminProjectResponse,
    AssessmentRatesUpdate,
    ProjectConflict,
    ProjectDetail,
)
from api.services.auth import current_admin
from api.v1._shared import clean_text

# Every route needs a signed-in admin. Its own router: /v1/admin also lets the scheduler's secret in
router = APIRouter(prefix="/v1/admin/projects", tags=["Admin projects"], dependencies=[Depends(current_admin)])

# The longest value each field takes
MAX_PROJECT_ID_LENGTH = 32
MAX_PROJECT_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 200
MAX_BOROUGH_LENGTH = 50
MAX_REGISTRATION_CODE_LENGTH = 50
# An assessment rate fits NUMERIC(10,4): below a million, to at most 4 decimal places
MAX_RATE = Decimal("1000000")
RATE_DECIMAL_PLACES = 4
# A project id is part of page URLs and Storage paths
_PROJECT_ID = re.compile(r"[A-Za-z0-9_-]+")


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
    project_id = clean_text(body.project_id, "project_id", MAX_PROJECT_ID_LENGTH)
    if not _PROJECT_ID.fullmatch(project_id):
        raise HTTPException(
            status_code=400, detail="project_id can only contain letters, digits, hyphens and underscores"
        )
    project_name = clean_text(body.project_name, "project_name", MAX_PROJECT_NAME_LENGTH)
    project_description = clean_text(body.project_description, "project_description", MAX_DESCRIPTION_LENGTH)
    borough = clean_text(body.borough, "borough", MAX_BOROUGH_LENGTH)
    registration_code = clean_text(
        body.registration_code, "registration_code", MAX_REGISTRATION_CODE_LENGTH, required=False
    )

    row = create_project(project_id, project_name, project_description, borough, registration_code)

    if row is None:
        conflict = ProjectConflict(detail="A project with this project_id already exists", existing_project_id=project_id)
        return JSONResponse(status_code=409, content=conflict.model_dump(mode="json"))

    return AdminProjectResponse(data=ProjectDetail.model_validate(row))


def _checked_rate(value: Optional[Decimal], field: str) -> Optional[Decimal]:
    """
    Check an assessment rate.
    Takes the rate (None clears it) and its field's name, for the message.
    Returns it; raises 400 for a negative rate, one of a million or more, or one past 4 decimal places.
    """
    if value is None:
        return None
    if not value.is_finite() or value < 0:
        raise HTTPException(status_code=400, detail=f"{field} cannot be negative")
    if value >= MAX_RATE:
        raise HTTPException(status_code=400, detail=f"{field} must be less than {MAX_RATE}")
    if -value.normalize().as_tuple().exponent > RATE_DECIMAL_PLACES:
        raise HTTPException(status_code=400, detail=f"{field} can have at most {RATE_DECIMAL_PLACES} decimal places")
    return value


@router.patch("/{project_id}/assessment-rates", response_model=AdminProjectResponse)
def set_assessment_rates(project_id: str, body: AssessmentRatesUpdate) -> AdminProjectResponse:
    """
    Set a project's DOT assessment rates, per square foot of 4" and 7" sidewalk. Admins only. A rate left out keeps its value; a null clears it.
    Takes the project id as a path parameter and either rate (or both) in the body.
    Returns an AdminProjectResponse with the project as it now is; raises 400 when nothing is sent or a rate doesn't fit, 403 for a user who isn't an admin, 404 for an unknown project and 422 for a rate that isn't a number.
    """
    rates = {field: _checked_rate(value, field) for field, value in body.model_dump(exclude_unset=True).items()}

    if not rates:
        raise HTTPException(status_code=400, detail="Send cost_per_sf_4in and/or cost_per_sf_7in")

    row = update_assessment_rates(project_id, rates)

    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")

    return AdminProjectResponse(data=ProjectDetail.model_validate(row))
