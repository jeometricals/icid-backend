from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Rectangle(BaseModel):
    """One of a bundle's measured rectangles, in feet."""

    L: float = Field(gt=0, lt=100000, allow_inf_nan=False)
    W: float = Field(gt=0, lt=100000, allow_inf_nan=False)

    model_config = {"extra": "forbid"}


class OuterDimensions(BaseModel):
    """What the OE measured of a bundle's outline: 1 to 3 rectangles (an L or a T takes 2 or 3)."""

    rectangles: list[Rectangle] = Field(min_length=1, max_length=3)

    model_config = {"extra": "forbid"}


class BundleIn(BaseModel):
    """POST /v1/pir-pages/{pir_page_id}/measurements/bundle: the flags to bundle and the outline measured."""

    flag_ids: list[UUID]
    outer_dimensions: OuterDimensions

    model_config = {"extra": "forbid"}


class MeasurementMember(BaseModel):
    """A flag measured by a measurement, with its part of it."""

    flag_id: UUID
    display_order: int
    pir_page_id: Optional[UUID] = None
    area_share_fraction: float
    computed_quantity: float
    quantity: float
    claimed_by_idr_id: Optional[UUID] = None
    deleted_at: Optional[datetime] = None


class MeasurementOut(BaseModel):
    """A measurement (Slices Y3a, Y3b): solo (one flag's own) or a bundle, with its members."""

    measurement_id: UUID
    pir_id: UUID
    property_id: UUID
    project_id: str
    outer_shape: str
    outer_dimensions: dict[str, Any]
    outer_area: float
    deductions: list[dict[str, Any]]
    needs_remeasure: bool
    pay_item_id: Optional[UUID] = None
    surveyed_thickness: Optional[str] = None
    surveyed_is_assessable: Optional[bool] = None
    bundled: bool
    frozen: bool
    claimed_by_idr_number: Optional[str] = None
    retired_at: Optional[datetime] = None
    created_at: datetime
    created_by: UUID
    updated_at: datetime
    updated_by: Optional[UUID] = None
    members: list[MeasurementMember]


class MeasurementResponse(BaseModel):
    """The response wrapping one measurement."""

    status: str
    message: str
    data: MeasurementOut
