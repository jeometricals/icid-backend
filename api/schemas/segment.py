from datetime import datetime
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
    """A curb run being drawn on a page, and the whole record a PATCH is checked as once merged over the stored segment: two or more points, in order. Its length is the server's, from the points and the page's scale."""

    segment_type: SegmentType
    points: list[SegmentPoint] = Field(min_length=2)
    pay_item_id: Optional[UUID] = None
    note: Optional[str] = Field(default=None, max_length=MAX_NOTE_LENGTH)


class SegmentUpdate(BaseModel):
    """What is changing on a segment. Any field may be left out. A null clears pay_item_id or note; on anything else it counts as left out."""

    segment_type: Optional[SegmentType] = None
    points: Optional[list[dict[str, Any]]] = None
    pay_item_id: Optional[UUID] = None
    note: Optional[str] = None


class SegmentOut(BaseModel):
    """A segment: a row of icid.segments, with its page's PIR, property and project. length_ft is in feet, from its points and the page's scale; None while the page has no scale."""

    segment_id: UUID
    pir_page_id: UUID
    pir_id: UUID
    property_id: UUID
    project_id: str
    segment_type: str
    points: list[dict[str, Any]]
    length_ft: Optional[float] = None
    pay_item_id: Optional[UUID] = None
    note: Optional[str] = None
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
