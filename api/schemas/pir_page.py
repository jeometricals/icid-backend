from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from api.schemas.pir import PirOut


class PirPageOut(BaseModel):
    """One page of a PIR's scan, with a signed URL to its PNG and when that URL stops working."""

    pir_page_id: UUID
    pir_id: UUID
    page_number: int
    image_url: str
    image_url_expires_at: datetime
    image_mime: str
    width_px: int
    height_px: int
    scale_px_per_ft: Optional[float] = None
    rotation_deg: int
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class PirPageResponse(BaseModel):
    status: str
    message: str
    data: PirPageOut


class PirPageListResponse(BaseModel):
    status: str
    message: str
    data: list[PirPageOut]


class PirPageUploadRequestIn(BaseModel):
    """
    Starting a PIR upload: the file's type (a PDF, PNG or JPEG), and optionally its name and size, which is refused
    up front when over the limit. content_type is R1d's name for mime (deprecated, removed in R1f).
    """

    filename: Optional[str] = None
    mime: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    content_type: Optional[str] = None


class PirPageUploadRequestOut(BaseModel):
    """
    Where to PUT the file, and the staging path to complete it with. upload_path and expires_in are R1d's names for
    the same (deprecated, removed in R1f).
    """

    upload_url: str
    staging_path: str
    expires_at: datetime
    upload_path: str
    expires_in: int


class PirPageUploadRequestResponse(BaseModel):
    status: str
    message: str
    data: PirPageUploadRequestOut


class PirPageUploadCompleteIn(BaseModel):
    """
    Finishing a PIR upload: the staging path the file was PUT to, and optionally a scale (pixels per foot) for every
    page. upload_path and scale are R1d's names for the same (deprecated, removed in R1f); the route refuses a scale
    that isn't above 0.
    """

    staging_path: Optional[str] = None
    scale_px_per_ft: Optional[Decimal] = Field(default=None, allow_inf_nan=False)
    upload_path: Optional[str] = None
    scale: Optional[float] = Field(default=None, allow_inf_nan=False)


class PirPageUploadCompleteOut(PirOut):
    """
    The PIR with its new pages. signed_url and expires_at are page 1's link, for R1d's image card (deprecated,
    removed in R1f).
    """

    pages: list[PirPageOut]
    signed_url: str
    expires_at: datetime


class PirPageUploadCompleteResponse(BaseModel):
    status: str
    message: str
    data: PirPageUploadCompleteOut


class PirPageScaleIn(BaseModel):
    """A page's scale in pixels per foot (the route refuses one that isn't above 0)."""

    scale_px_per_ft: Decimal = Field(allow_inf_nan=False)


class PirPageRotationIn(BaseModel):
    """A page's rotation: a quarter turn at a time."""

    rotation_deg: Literal[0, 90, 180, 270]
