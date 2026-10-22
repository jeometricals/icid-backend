from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SegmentType = Literal[
    "curb_concrete", "curb_concrete_depressed", "curb_steel_faced", "curb_steel_faced_corner", "curb_granite", "other",
]
MAX_NOTE_LENGTH = 2000


class SegmentPoint(BaseModel):
    """One point of a segment, in the page's native pixels."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class SegmentCreate(BaseModel):
    """A curb run being drawn on a page, and the whole record a PATCH is checked as once merged over the stored segment: two or more points, in order. Its length is the server's, from the points and the page's scale; a reported length (0 or more) overrides it for billing."""

    segment_type: SegmentType
    points: list[SegmentPoint] = Field(min_length=2)
    pay_item_id: Optional[UUID] = None
    note: Optional[str] = Field(default=None, max_length=MAX_NOTE_LENGTH)
    reported_length_ft: Optional[Decimal] = Field(default=None, ge=0, allow_inf_nan=False)
    length_delta_reason: Optional[str] = None


class SegmentUpdate(BaseModel):
    """What is changing on a segment. Any field may be left out. A null clears pay_item_id, note or length_delta_reason, and on reported_length_ft reverts to the drawn length (clearing length_delta_reason); on anything else it counts as left out."""

    segment_type: Optional[SegmentType] = None
    points: Optional[list[dict[str, Any]]] = None
    pay_item_id: Optional[UUID] = None
    note: Optional[str] = None
    reported_length_ft: Optional[Decimal] = None
    length_delta_reason: Optional[str] = None


class SegmentOut(BaseModel):
    """A segment: a row of icid.segments, with its page's PIR, property and project. length_ft is in feet, from its points and the page's scale; None while the page has no scale. reported_length_ft overrides it (None for none); effective_length_ft is what counts (the reported length, or else the drawn one) and is_overridden says which. claimed_by_idr_id and constructed_on are set while an approved IDR claims it."""

    segment_id: UUID
    pir_page_id: UUID
    pir_id: UUID
    property_id: UUID
    project_id: str
    segment_type: str
    points: list[dict[str, Any]]
    length_ft: Optional[float] = None
    reported_length_ft: Optional[float] = None
    length_delta_reason: Optional[str] = None
    effective_length_ft: Optional[float] = None
    is_overridden: bool = False
    pay_item_id: Optional[UUID] = None
    note: Optional[str] = None
    claimed_by_idr_id: Optional[UUID] = None
    constructed_on: Optional[date] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class SegmentResponse(BaseModel):
    status: str
    message: str
    data: SegmentOut


class SegmentListResponse(BaseModel):
    status: str
    message: str
    data: list[SegmentOut]
