from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from api.schemas.general_form import GeneralFormData


class ReportCreate(BaseModel):
    project_id: str
    reporter_uuid: UUID
    report_date: Optional[date] = None


class Report(BaseModel):
    report_id: UUID
    reporter_uuid: UUID
    project_id: str
    report_date: Optional[date] = None
    status: str
    created_at: datetime
    updated_at: datetime


class ReportResponse(BaseModel):
    status: str
    message: str
    data: Report


class GeneralFormSaved(BaseModel):
    report_id: UUID
    completed_form_id: str
    saved_at: datetime


class GeneralFormSaveResponse(BaseModel):
    status: str
    message: str
    data: GeneralFormSaved


class ReportWithGeneral(Report):
    general_form: Optional[GeneralFormData] = None


class ReportWithGeneralResponse(BaseModel):
    status: str
    message: str
    data: ReportWithGeneral
