"""
A project's ped ramp inspection listing (Pedramp Phase 3, S5): one row per corner with an inspection, and the bulk
download of the approved ones' DOT PDFs.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ListingStatus = Literal["draft", "submitted", "approved"]
YesNo = Literal["yes", "no"]
# The longest search text
MAX_SEARCH = 200


class InspectionListingFilters(BaseModel):
    """The listing's filters as the bulk download's body takes them; the dates are YYYY-MM-DD text, read by the route."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Optional[ListingStatus] = None
    inspector_uuid: Optional[UUID] = None
    re_uuid: Optional[UUID] = None
    date_from: Optional[str] = Field(default=None, alias="from")
    date_to: Optional[str] = Field(default=None, alias="to")
    has_tif: Optional[YesNo] = None
    q: Optional[str] = Field(default=None, max_length=MAX_SEARCH)


class InspectionListItem(BaseModel):
    """One corner's inspection in the listing: the corner, its live ramps, the status, who signed, when it was approved and its TIFs."""

    model_config = ConfigDict(extra="ignore")

    inspection_id: UUID
    corner_id: UUID
    corner_name: str
    corner_direction: Optional[str] = None
    ramp_count: int
    status: ListingStatus
    inspector_uuid: Optional[UUID] = None
    inspector_name: Optional[str] = None
    re_uuid: Optional[UUID] = None
    re_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    tif_count: int
    has_dot_pdf: bool


class InspectionListing(BaseModel):
    """A page of the listing: how many match in all, the page asked for and its rows."""

    total: int
    page: int
    page_size: int
    rows: list[InspectionListItem]


class InspectionListingResponse(BaseModel):
    """The envelope around a page of the listing."""

    status: str
    message: str
    data: InspectionListing


class BulkDownloadOut(BaseModel):
    """The bulk ZIP: a signed URL to it, when that expires, how many corners it holds and the name it saves as."""

    url: str
    expires_at: datetime
    corner_count: int
    file_name: str


class BulkDownloadResponse(BaseModel):
    """The envelope around the bulk download's link."""

    status: str
    message: str
    data: BulkDownloadOut
