"""
Ped ramp photos (Pedramp Phase 3, S4): an inspection's photos, each of one ramp, and a TIF's photos and site layout.
"""

from datetime import datetime
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

PhotoKind = Literal["photo", "layout"]
# The longest caption and file name
MAX_CAPTION = 200
MAX_FILE_NAME = 255
# The highest photo number
MAX_PHOTO_NUMBER = 999


def _caption(value: Optional[str]) -> Optional[str]:
    """
    Trim a caption; a blank one is none.
    Takes the caption (None for none).
    Returns it trimmed, or None.
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


# A caption as every body takes it: at most MAX_CAPTION characters, trimmed, blank as none
Caption = Annotated[Optional[str], Field(max_length=MAX_CAPTION), AfterValidator(_caption)]


class InspectionPhotoUploadIn(BaseModel):
    """Asking to upload an inspection's photo: the ramp it shows and, optionally, the file's name."""

    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    file_name: Optional[str] = Field(default=None, max_length=MAX_FILE_NAME)


class TifPhotoUploadIn(BaseModel):
    """Asking to upload a TIF's photo or site layout: optionally, the file's name."""

    model_config = ConfigDict(extra="forbid")

    file_name: Optional[str] = Field(default=None, max_length=MAX_FILE_NAME)
    kind: PhotoKind = "photo"


class PhotoUploadOut(BaseModel):
    """Where to PUT the photo: the signed URL, the staging path to complete with and when the URL expires."""

    upload_url: str
    staging_path: str
    expires_at: datetime


class PhotoUploadResponse(BaseModel):
    """The envelope around a photo's upload link."""

    status: str
    message: str
    data: PhotoUploadOut


class InspectionPhotoCreate(BaseModel):
    """Completing an inspection's photo: the staged file, the ramp, its number on the form and an optional caption."""

    model_config = ConfigDict(extra="forbid")

    storage_path: str = Field(min_length=1)
    property_id: UUID
    photo_number: int = Field(ge=1, le=MAX_PHOTO_NUMBER)
    caption: Caption = None


class TifPhotoCreate(BaseModel):
    """Completing a TIF's photo (numbered) or its site layout (numbered 1, whatever is sent)."""

    model_config = ConfigDict(extra="forbid")

    storage_path: str = Field(min_length=1)
    kind: PhotoKind = "photo"
    photo_number: Optional[int] = Field(default=None, ge=1, le=MAX_PHOTO_NUMBER)
    caption: Caption = None


class PhotoUpdate(BaseModel):
    """What is changing on a photo: its number, its caption (null or blank clears it), or both."""

    model_config = ConfigDict(extra="forbid")

    photo_number: Optional[int] = Field(default=None, ge=1, le=MAX_PHOTO_NUMBER)
    caption: Caption = None


class PhotoOut(BaseModel):
    """A photo: a row of icid.ped_ramp_photos without its Storage path, with a signed URL to its file."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    inspection_id: Optional[UUID] = None
    tif_id: Optional[UUID] = None
    property_id: Optional[UUID] = None
    kind: PhotoKind
    photo_number: int
    caption: Optional[str] = None
    content_type: Optional[str] = None
    byte_size: Optional[int] = None
    uploaded_at: datetime
    uploaded_by: UUID
    image_url: str
    image_url_expires_at: datetime


class PhotoResponse(BaseModel):
    """The envelope around one photo."""

    status: str
    message: str
    data: PhotoOut


class PhotoListResponse(BaseModel):
    """The envelope around a form's photos."""

    status: str
    message: str
    data: list[PhotoOut]
