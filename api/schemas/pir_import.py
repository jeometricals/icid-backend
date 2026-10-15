from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from api.schemas.feature import FeatureCreate
from api.schemas.flag import FlagCreate


class FlagImportItem(FlagCreate):
    """
    A flag in a Smart PIR import: a flag as FlagCreate checks it, with its non-assessable areas. Each area is checked
    on its own (as NonAssessableAreaCreate), so a bad one is dropped and the flag kept.
    """

    non_assessable_areas: list[dict[str, Any]] = []


class FeatureImportItem(FeatureCreate):
    """
    A feature in a Smart PIR import: a feature as FeatureCreate checks it, except that it names its flag by
    flag_index, the flag's place in its page's flags list (0 first), never by flag_id.
    """

    flag_index: Optional[int] = Field(default=None, ge=0)


class SmartPirPageImport(BaseModel):
    """
    One page of a Smart PIR import, by its page number. scale_px_per_ft and rotation_deg, when given, are set on the
    page. Every item is checked on its own (flags as FlagImportItem, features as FeatureImportItem, segments as
    SegmentCreate, annotations as PirAnnotationIn), so a bad one is dropped and the rest imported.
    """

    page_number: int = Field(ge=1)
    scale_px_per_ft: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    rotation_deg: Optional[Literal[0, 90, 180, 270]] = None
    annotations: Optional[dict[str, Any]] = None
    flags: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []


class SmartPirImport(BaseModel):
    """
    A Smart PIR import file: what a sketch tool drew on a PIR's pages, added to what they have. pir_id must be the
    PIR in the path. annotations here are page 1's title block, unless page 1 brings its own.
    """

    schema_version: Literal["1.0"]
    pir_id: UUID
    annotations: Optional[dict[str, Any]] = None
    pages: list[SmartPirPageImport]


class ImportCounts(BaseModel):
    """How many of each kind of item."""

    flags: int = 0
    non_assessable_areas: int = 0
    features: int = 0
    segments: int = 0
    annotations: int = 0


class DroppedItem(BaseModel):
    """
    An item an import left out, and why. page_number is the page entry it was in (None for the file's own
    annotations); index its place in its list; flag_index, for a non-assessable area, its flag's place.
    """

    kind: Literal["flag", "non_assessable_area", "feature", "segment", "annotations"]
    page_number: Optional[int] = None
    index: Optional[int] = None
    flag_index: Optional[int] = None
    reason: str


class PirImportResult(BaseModel):
    """
    What an import did: what it inserted, what it dropped (checked out, or lost to a change made meanwhile), each
    dropped item that failed its check, and how many pages took a new scale or rotation.
    """

    inserted: ImportCounts
    dropped: ImportCounts
    dropped_items: list[DroppedItem]
    pages_updated: int


class PirImportResponse(BaseModel):
    status: str
    message: str
    data: PirImportResult
