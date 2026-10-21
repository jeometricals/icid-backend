"""
A property's work across IDRs (Slice V): its work history, every time a report imported it (V2), and the pay items
IDRs report on it (V5).
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


class PayItemIdr(BaseModel):
    """What one IDR reports of a pay item on a property: its number, work date, status, inspector and quantity."""

    idr_id: UUID
    idr_number: Optional[str] = None
    report_date: date
    status: str
    inspector: Optional[str] = None
    quantity: float


class PropertyPayItem(BaseModel):
    """A pay item reported on a property: its total across IDRs (a flag or segment once), and each IDR's part."""

    contract_item_id: UUID
    pay_item_ref: str
    budget_code: str
    description: str
    unit: str
    total: float
    idrs: list[PayItemIdr]


class PropertyPayItemsResponse(BaseModel):
    status: str
    message: str
    data: list[PropertyPayItem]
