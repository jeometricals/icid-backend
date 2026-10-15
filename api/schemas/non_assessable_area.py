from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from api.schemas.flag import DIMENSIONS_BY_SHAPE, FlagShape, checked_dimensions

NonAssessableReason = Literal[
    "driveway", "grass", "dirt", "existing_sidewalk", "asphalt", "tree_pit", "landmark", "other",
]


class NonAssessableAreaCreate(BaseModel):
    """
    A part of a flag that is not assessed, being added to it, and the whole record a PATCH is checked as once merged
    over the stored area. Its dimensions are checked against its shape, as a flag's are; sketch_x / sketch_y /
    rotation are its place on the flag's page, in the page's native pixels.
    """

    display_order: int = Field(ge=1)
    shape: FlagShape
    dimensions: dict[str, Any]
    sketch_x: float = Field(allow_inf_nan=False)
    sketch_y: float = Field(allow_inf_nan=False)
    rotation: float = Field(default=0, allow_inf_nan=False)
    reason: NonAssessableReason

    @model_validator(mode="after")
    def _check(self) -> "NonAssessableAreaCreate":
        """
        Check the dimensions against the shape, keeping them as parsed.
        Takes the parsed area.
        Returns it; raises ValueError when they don't fit.
        """
        self.dimensions = checked_dimensions(self.shape, self.dimensions, DIMENSIONS_BY_SHAPE)
        return self


class NonAssessableAreaUpdate(BaseModel):
    """What is changing on a non-assessable area. Any field may be left out, and a null counts as left out. The merged record is checked as NonAssessableAreaCreate."""

    display_order: Optional[int] = None
    shape: Optional[FlagShape] = None
    dimensions: Optional[dict[str, Any]] = None
    sketch_x: Optional[float] = None
    sketch_y: Optional[float] = None
    rotation: Optional[float] = None
    reason: Optional[NonAssessableReason] = None


class NonAssessableAreaOut(BaseModel):
    """A non-assessable area: a row of icid.non_assessable_areas, with its flag's page, PIR, property and project. computed_quantity is its shape's area, taken off the flag's."""

    non_assessable_area_id: UUID
    flag_id: UUID
    pir_page_id: Optional[UUID] = None
    pir_id: UUID
    property_id: UUID
    project_id: str
    display_order: int
    shape: str
    dimensions: dict[str, Any]
    sketch_x: float
    sketch_y: float
    rotation: float
    reason: str
    computed_quantity: float
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class NonAssessableAreaResponse(BaseModel):
    status: str
    message: str
    data: NonAssessableAreaOut


class NonAssessableAreaListResponse(BaseModel):
    status: str
    message: str
    data: list[NonAssessableAreaOut]
