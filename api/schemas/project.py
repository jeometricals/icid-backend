from pydantic import BaseModel
from typing import Optional


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


class ProjectDetailResponse(BaseModel):
    status: str
    message: str
    data: ProjectDetail
