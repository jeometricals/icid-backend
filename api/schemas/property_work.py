"""
A property's work across IDRs (Slice V): its work history, every time a report imported it (V2).
"""

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkHistoryEntry(BaseModel):
    """One import of a property into a report: the work, and the IDR's number, day, status and inspector."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    idr_id: UUID
    report_id: UUID
    report_type: str
    work_type: Literal["excavation", "concrete", "pavement_restoration", "other"]
    work_type_other: Optional[str] = None
    added_at: datetime
    idr_number: Optional[str] = None
    report_date: date
    status: str
    deleted_at: Optional[datetime] = None
    return_reason: Optional[str] = None
    reporter_uuid: UUID
    reporter_name: Optional[str] = None


class WorkHistoryResponse(BaseModel):
    status: str
    message: str
    data: list[WorkHistoryEntry]
