from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class ProjectListItem(BaseModel):
    project_id: str
    project_name: str
    borough: Optional[str] = None
    status: Optional[str] = None
    user_role: Optional[str] = None
    # The roles the user holds on the project: any of 'inspector', 'oe', 're'
    roles: list[str] = []


class ProjectListResponse(BaseModel):
    status: str
    message: str
    data: list[ProjectListItem]


class ProjectDetail(BaseModel):
    project_id: str
    project_name: str
    project_description: Optional[str] = None
    registration_code: Optional[str] = None
    borough: Optional[str] = None
    status: Optional[str] = None
    # The DOT assessment rates, per square foot of 4" and 7" sidewalk
    cost_per_sf_4in: Optional[float] = None
    cost_per_sf_7in: Optional[float] = None


class ProjectDetailResponse(BaseModel):
    status: str
    message: str
    data: ProjectDetail


class AdminProjectCreate(BaseModel):
    """A project an admin is creating. The route checks every value itself, so a bad one is a 400."""

    project_id: str
    project_name: str
    project_description: str
    borough: str
    registration_code: Optional[str] = None


class AssessmentRatesUpdate(BaseModel):
    """
    A project's DOT assessment rates an admin is setting, per square foot. Either may be left out (it keeps its
    value); a null clears it. The route checks the values itself, so a bad one is a 400.
    """

    cost_per_sf_4in: Optional[Decimal] = None
    cost_per_sf_7in: Optional[Decimal] = None


class ProjectConflict(BaseModel):
    """The 409 body when a project id is taken: the reason, and the id."""

    detail: str
    existing_project_id: str


class AdminProjectResponse(BaseModel):
    """One project, as an admin sees it."""

    data: ProjectDetail


class AdminProjectListResponse(BaseModel):
    """Every project, as an admin sees them."""

    data: list[ProjectDetail]


class ProjectRoleChange(BaseModel):
    """Giving a user a role on a project, or taking it away."""

    user_uuid: UUID
    role: Literal["inspector", "oe", "re"]
    action: Literal["grant", "revoke"]


class ProjectMemberRole(BaseModel):
    """One role one user holds on a project."""

    user_uuid: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str
    assigned_at: datetime


class ProjectRolesResponse(BaseModel):
    status: str
    message: str
    data: list[ProjectMemberRole]
