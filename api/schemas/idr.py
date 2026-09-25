from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from api.schemas.idr_report import IdrReport


class IdrCreate(BaseModel):
    project_id: str
    reporter_uuid: UUID
    report_date: date


class Idr(BaseModel):
    idr_id: UUID
    project_id: str
    reporter_uuid: UUID
    report_date: date
    work_start_time: Optional[time] = None
    work_end_time: Optional[time] = None
    inspector_start_time: Optional[time] = None
    inspector_end_time: Optional[time] = None
    temp_low: Optional[float] = None
    temp_high: Optional[float] = None
    weather_am: Optional[str] = None
    weather_pm: Optional[str] = None
    total_pages: Optional[int] = None
    status: str
    submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IdrResponse(BaseModel):
    status: str
    message: str
    data: Idr


class IdrWithReports(Idr):
    reports: list[IdrReport]


class IdrWithReportsResponse(BaseModel):
    status: str
    message: str
    data: IdrWithReports


class IdrConflict(BaseModel):
    detail: str
    existing_idr_id: UUID
