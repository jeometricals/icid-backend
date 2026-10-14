import math
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.services.flag_area import polygon_area

FlagShape = Literal["rectangle", "trapezoid", "triangle", "polygon", "arc"]
FlagMark = Literal["number_only", "cross", "cross_with_number", "tick", "none"]
FlagNonAssessableReason = Literal[
    "tree_damage", "city_tree", "transition", "no_driveway", "hydrant", "fronting_city", "utility", "settlement", "other",
]
FlagThickness = Literal["4in", "7in", "7in_as_4in"]

# A length in feet: a finite number above zero
Length = Field(gt=0, allow_inf_nan=False)


class _Dimensions(BaseModel):
    """What every shape's dimensions share: no keys beyond its own."""

    model_config = ConfigDict(extra="forbid")


class RectangleDimensions(_Dimensions):
    """A rectangle: length and width."""

    L: float = Length
    W: float = Length


class TrapezoidDimensions(_Dimensions):
    """A trapezoid: the two parallel sides and the width between them."""

    a: float = Length
    b: float = Length
    w: float = Length


class TriangleDimensions(_Dimensions):
    """A right triangle: its two legs."""

    a: float = Length
    b: float = Length


class Vertex(BaseModel):
    """One corner of a polygon, in feet on the sketch."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class PolygonDimensions(_Dimensions):
    """A polygon: three or more vertices, in order, enclosing some area."""

    vertices: list[Vertex] = Field(min_length=3)

    @model_validator(mode="after")
    def _encloses_an_area(self) -> "PolygonDimensions":
        """
        Refuse a degenerate polygon (collinear points, or all on one spot).
        Takes the parsed polygon.
        Returns it; raises ValueError when it encloses no area.
        """
        if polygon_area([vertex.model_dump() for vertex in self.vertices]) == 0:
            raise ValueError("a polygon's vertices must enclose an area")
        return self


class ArcDimensions(_Dimensions):
    """A circular segment: the radius and the angle it spans, in radians, above 0 and at most 2 pi."""

    r: float = Length
    theta: float = Field(gt=0, le=2 * math.pi, allow_inf_nan=False)


# The dimensions each shape takes
DIMENSIONS_BY_SHAPE: dict[str, type[_Dimensions]] = {
    "rectangle": RectangleDimensions,
    "trapezoid": TrapezoidDimensions,
    "triangle": TriangleDimensions,
    "polygon": PolygonDimensions,
    "arc": ArcDimensions,
}


class FlagCreate(BaseModel):
    """
    A flag being added to a PIR, and the whole record a PATCH is checked as once merged over the stored flag.
    Its dimensions are checked against its shape, and the survey's rules hold: an assessable flag has no
    non-assessable reason and any other has one; a flag added in the field, and only such a flag, has the mark
    'none'; an override (reported_quantity) is above zero.
    """

    display_order: int = Field(ge=0)
    shape: FlagShape
    dimensions: dict[str, Any]
    sketch_x: Optional[float] = Field(default=None, allow_inf_nan=False)
    sketch_y: Optional[float] = Field(default=None, allow_inf_nan=False)
    rotation: float = Field(default=0, allow_inf_nan=False)
    surveyed_mark: FlagMark
    surveyed_is_assessable: bool
    surveyed_non_assessable_reason: Optional[FlagNonAssessableReason] = None
    surveyed_thickness: FlagThickness
    added_in_field: bool = False
    pay_item_id: Optional[UUID] = None
    reported_quantity: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)
    quantity_delta_reason: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "FlagCreate":
        """
        Check the dimensions against the shape (keeping them as parsed) and the survey's rules.
        Takes the parsed flag.
        Returns it; raises ValueError naming the first rule broken.
        """
        self.dimensions = DIMENSIONS_BY_SHAPE[self.shape].model_validate(self.dimensions).model_dump()
        if self.surveyed_is_assessable and self.surveyed_non_assessable_reason is not None:
            raise ValueError("an assessable flag has no surveyed_non_assessable_reason")
        if not self.surveyed_is_assessable and self.surveyed_non_assessable_reason is None:
            raise ValueError("a flag that isn't assessable needs a surveyed_non_assessable_reason")
        if self.surveyed_mark == "none" and not self.added_in_field:
            raise ValueError("surveyed_mark 'none' is only for a flag added in the field")
        if self.added_in_field and self.surveyed_mark != "none":
            raise ValueError("a flag added in the field has surveyed_mark 'none'")
        return self


class FlagUpdate(BaseModel):
    """
    What is changing on a flag. Any field may be left out. A null counts as left out, except for sketch_x,
    sketch_y, surveyed_non_assessable_reason, pay_item_id and quantity_delta_reason, where it clears the field,
    and reported_quantity, where it reverts to the computed quantity. The merged record is checked as FlagCreate.
    """

    display_order: Optional[int] = None
    shape: Optional[FlagShape] = None
    dimensions: Optional[dict[str, Any]] = None
    sketch_x: Optional[float] = None
    sketch_y: Optional[float] = None
    rotation: Optional[float] = None
    surveyed_mark: Optional[FlagMark] = None
    surveyed_is_assessable: Optional[bool] = None
    surveyed_non_assessable_reason: Optional[FlagNonAssessableReason] = None
    surveyed_thickness: Optional[FlagThickness] = None
    added_in_field: Optional[bool] = None
    pay_item_id: Optional[UUID] = None
    reported_quantity: Optional[Decimal] = None
    quantity_delta_reason: Optional[str] = None


class FlagOut(BaseModel):
    """
    A flag: a row of icid.flags, with its PIR's property and project. quantity is what counts (the override, or
    else the computed area) and is_overridden says which.
    """

    id: UUID
    pir_id: UUID
    property_id: UUID
    project_id: str
    display_order: int
    added_in_field: bool
    shape: str
    dimensions: dict[str, Any]
    sketch_x: Optional[float] = None
    sketch_y: Optional[float] = None
    rotation: float
    surveyed_mark: str
    surveyed_is_assessable: bool
    surveyed_non_assessable_reason: Optional[str] = None
    surveyed_thickness: str
    is_constructed: bool
    pay_item_id: Optional[UUID] = None
    computed_quantity: float
    reported_quantity: Optional[float] = None
    quantity: float
    is_overridden: bool
    quantity_delta_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class FlagResponse(BaseModel):
    status: str
    message: str
    data: FlagOut


class FlagListResponse(BaseModel):
    status: str
    message: str
    data: list[FlagOut]
