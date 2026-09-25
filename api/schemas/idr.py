from datetime import date, datetime, time
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError

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


class IdrHeaderUpdate(BaseModel):
    """
    Partial IDR header update: omitted fields are left alone, null clears a field.
    Only the eight header fields are accepted; any other key is a 422 naming it.
    """

    model_config = ConfigDict(extra="forbid")

    work_start_time: Optional[time] = None
    work_end_time: Optional[time] = None
    inspector_start_time: Optional[time] = None
    inspector_end_time: Optional[time] = None
    temp_low: Optional[float] = None
    temp_high: Optional[float] = None
    weather_am: Optional[str] = None
    weather_pm: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def reject_non_header_fields(cls, data: Any) -> Any:
        """
        Reject any key that is not one of the eight header fields, naming the offenders.
        Takes the raw request body.
        Returns it unchanged, or raises a validation error (surfaced as 422).
        """
        if not isinstance(data, dict):
            return data

        extra = sorted(set(data) - set(cls.model_fields))

        if len(extra) == 1:
            raise PydanticCustomError(
                "header_field_not_editable",
                "Field '{field}' cannot be edited via this endpoint.",
                {"field": extra[0]},
            )

        if extra:
            raise PydanticCustomError(
                "header_field_not_editable",
                "Fields {fields} cannot be edited via this endpoint.",
                {"fields": ", ".join(f"'{name}'" for name in extra)},
            )

        return data


class IdrWithReports(Idr):
    reports: list[IdrReport]


class IdrWithReportsResponse(BaseModel):
    status: str
    message: str
    data: IdrWithReports


class IdrConflict(BaseModel):
    detail: str
    existing_idr_id: UUID
