from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PirCreate(BaseModel):
    """A PIR an admin is digitizing for a property. The route trims the initials; a malformed date is a 422."""

    inspector_initials: str
    inspector_sign_date: date


class PirUpdate(BaseModel):
    """What an admin is changing on a PIR. Any field may be left out; a null counts as left out."""

    inspector_initials: Optional[str] = None
    inspector_sign_date: Optional[date] = None


class PirOut(BaseModel):
    """
    A PIR: a row of icid.pirs, with its property's project. deleted_at and deleted_by are None for one that isn't
    deleted; the source image and the as-built signature stay None until the slices that set them.
    """

    id: UUID
    property_id: UUID
    project_id: str
    source_image_url: Optional[str] = None
    source_image_scale: Optional[float] = None
    inspector_initials: str
    inspector_sign_date: date
    digitized_by: UUID
    digitized_at: datetime
    as_built_signed_by: Optional[UUID] = None
    as_built_signed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class PirResponse(BaseModel):
    status: str
    message: str
    data: PirOut


class PirListResponse(BaseModel):
    status: str
    message: str
    data: list[PirOut]


class PirConflict(BaseModel):
    """The 409 body when a property already has a live PIR: the reason, and that PIR."""

    detail: str
    existing_pir_id: UUID


class PirImageUploadRequest(BaseModel):
    """Starting a PIR image upload: the type of the file the client will PUT (a PDF, PNG or JPEG)."""

    content_type: str


class PirImageUploadUrl(BaseModel):
    """Where to PUT a PIR's image file, and the path to complete it with."""

    upload_url: str
    upload_path: str
    expires_in: int


class PirImageUploadUrlResponse(BaseModel):
    status: str
    message: str
    data: PirImageUploadUrl


class PirImageComplete(BaseModel):
    """
    Finishing a PIR image upload: the path the file was PUT to, and the image's scale in pixels per foot (15 when
    left out; the route refuses one that isn't above 0).
    """

    upload_path: str
    scale: float = Field(default=15, allow_inf_nan=False)


class PirImageScale(BaseModel):
    """A new scale for a PIR's image, in pixels per foot (the route refuses one that isn't above 0)."""

    scale: float = Field(allow_inf_nan=False)


class PirWithImage(PirOut):
    """A PIR just given an image, with a signed URL to the image and when that URL stops working."""

    signed_url: str
    expires_at: datetime


class PirWithImageResponse(BaseModel):
    status: str
    message: str
    data: PirWithImage


class PirImage(BaseModel):
    """A link to a PIR's image: the signed URL, when it stops working, and the image's scale (pixels per foot)."""

    signed_url: str
    expires_at: datetime
    scale: Optional[float] = None


class PirImageResponse(BaseModel):
    status: str
    message: str
    data: PirImage
