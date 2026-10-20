"""
A ped ramp corner's inspection (Pedramp Phase 3, S1): the review and signature wrapper around the corner and its ramps'
attributes. Draft, then submitted (the inspector signs), then approved (the RE signs); a return sends it back to draft
with the RE's comment.
"""

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

InspectionStatus = Literal["draft", "submitted", "approved"]
# The longest return comment
MAX_RETURN_COMMENT = 2000


class InspectionUpdate(BaseModel):
    """What is changing on an inspection: the inspector's answer to "are all ADA requirements met?" (null clears it)."""

    model_config = ConfigDict(extra="forbid")

    requirements_met: Optional[bool]


class InspectionReturn(BaseModel):
    """An RE's return of a submitted inspection: the comment for its inspector, required, trimmed."""

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(max_length=MAX_RETURN_COMMENT)

    @field_validator("comment")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """
        Trim the comment and refuse a blank one.
        Takes the comment.
        Returns it trimmed; raises ValueError when blank.
        """
        value = value.strip()
        if not value:
            raise ValueError("A return needs a comment")
        return value


class InspectionOut(BaseModel):
    """An inspection: a row of icid.ped_ramp_inspections, with its corner's project and streets, who signed and returned it, and its TIF counts."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    corner_id: UUID
    project_id: str
    north_south_street: str
    east_west_street: str
    corner_direction: Optional[str] = None
    status: InspectionStatus
    requirements_met: Optional[bool] = None
    inspector_uuid: Optional[UUID] = None
    inspector_name: Optional[str] = None
    submitted_at: Optional[datetime] = None
    inspector_signed_at: Optional[datetime] = None
    re_uuid: Optional[UUID] = None
    re_name: Optional[str] = None
    re_signed_at: Optional[datetime] = None
    return_comment: Optional[str] = None
    returned_at: Optional[datetime] = None
    returned_by: Optional[UUID] = None
    returned_by_name: Optional[str] = None
    dot_pdf_path: Optional[str] = Field(default=None, exclude=True)
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    tif_count: int = 0
    draft_tif_count: int = 0

    @computed_field
    @property
    def has_dot_pdf(self) -> bool:
        """
        Say whether the DOT form was filled and stored at approval.
        Takes the inspection.
        Returns True when it was.
        """
        return self.dot_pdf_path is not None


class InspectionResponse(BaseModel):
    status: str
    message: str
    data: InspectionOut


class CornerInspectionResponse(BaseModel):
    """A corner's inspection, or null when none has been started."""

    status: str
    message: str
    data: Optional[InspectionOut]


class DownloadOut(BaseModel):
    """A file in the pedramp-exports bucket, handed out through a signed URL: never the bytes (Vercel's 4.5 MB limit)."""

    url: str
    expires_at: datetime
    file_name: str


class DownloadResponse(BaseModel):
    status: str
    message: str
    data: DownloadOut
