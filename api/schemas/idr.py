from datetime import date, datetime, time
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError

from api.schemas.field_edit import FieldEdit
from api.schemas.idr_report import IdrReport


# The statuses an IDR can be listed by ('returned' is unused; 'deleted' matches nothing unless an admin asks for
# deleted IDRs)
IdrStatus = Literal["draft", "submitted", "stage1_review", "stage2_review", "approved", "deleted"]


class IdrCreate(BaseModel):
    project_id: str
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
    has_dismissed_auto_general: bool = False
    status: str
    submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    inspector_signature_path: Optional[str] = None
    inspector_signed_at: Optional[datetime] = None
    idr_number: Optional[str] = None
    stage1_reviewer_uuid: Optional[UUID] = None
    stage1_accepted_at: Optional[datetime] = None
    stage1_reviewed_at: Optional[datetime] = None
    re_reviewer_uuid: Optional[UUID] = None
    stage2_accepted_at: Optional[datetime] = None
    re_signature_path: Optional[str] = None
    re_signed_at: Optional[datetime] = None
    return_reason: Optional[str] = None
    returned_from: Optional[str] = None
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class IdrResponse(BaseModel):
    status: str
    message: str
    data: Idr


class IdrListItem(Idr):
    report_count: int
    has_general: bool
    reporter_name: Optional[str] = None
    stage1_reviewer_name: Optional[str] = None
    re_reviewer_name: Optional[str] = None


class IdrListResponse(BaseModel):
    status: str
    message: str
    data: list[IdrListItem]


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
    # Every edit reviewers made on the IDR, oldest first; the IDR and its reports already hold the edited values
    field_edits: list[FieldEdit] = []


class IdrWithReportsResponse(BaseModel):
    status: str
    message: str
    data: IdrWithReports


class IdrConflict(BaseModel):
    detail: str
    existing_idr_id: UUID


class StageOneAccept(BaseModel):
    """What a reviewer sends when picking an IDR up for Stage 1. The number is needed only the first time through."""

    idr_number: Optional[str] = None


class IdrReturn(BaseModel):
    """Sending an IDR back from review: who it goes to, and why."""

    to: Literal["inspector", "oe"]
    comment: str
