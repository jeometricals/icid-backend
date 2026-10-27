from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api.schemas.match_line import MatchLinePoint


class SidewalkStripIn(BaseModel):
    """
    A page's sidewalk strip as sent (Slice Y1.5): its points in the page's native pixels, the property line first
    (points 0 -> 1), the polygon closing from the last point back to the first; null clears the strip.
    """

    model_config = ConfigDict(extra="forbid")

    points: Optional[list[MatchLinePoint]]


class StripFlag(BaseModel):
    """A flag the strip found wholly outside it: its id, its number on the page and, for a claimed one, its IDR."""

    id: UUID
    display_order: int
    claimed_by_idr_id: Optional[UUID] = None


class SidewalkStrip(BaseModel):
    """
    A page's sidewalk strip as saved: its points (None once cleared), source, when and by whom it was set, its frontage
    (the first edge, at the page's scale), the flags it marked non-assessable and the flags outside it that it left as
    they were because an approved IDR claimed them (for an admin to review).
    """

    pir_page_id: UUID
    points: Optional[list[MatchLinePoint]] = None
    source: Optional[str] = None
    set_at: Optional[datetime] = None
    set_by: Optional[UUID] = None
    frontage_ft: Optional[float] = None
    flipped: list[StripFlag] = []
    outside_claimed: list[StripFlag] = []


class SidewalkStripResponse(BaseModel):
    status: str
    message: str
    data: SidewalkStrip

