from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from api.schemas.flag import FEATURE_DIMENSIONS_BY_SHAPE, checked_dimensions

FeatureType = Literal[
    "manhole", "grate", "utility_cover", "hydrant", "fire_alarm", "tree_pit", "meter", "sign_or_meter", "mail_box",
    "telephone", "cellar_door", "pole", "detectable_warning", "other",
]
AreaImpact = Literal["marker_only", "subtract", "sits_on_top"]
FeatureShape = Literal["rectangle", "trapezoid", "triangle", "polygon", "arc", "circle"]
MAX_LABEL_LENGTH = 200


class FeatureCreate(BaseModel):
    """
    A point obstruction or sized feature being drawn on a page, and the whole record a PATCH is checked as once
    merged over the stored feature. sketch_x / sketch_y / rotation are its place on the page, in native pixels. A
    shape and its dimensions come together, checked as a flag's are (a circle takes {r}); a feature that subtracts or
    sits on top needs a shape, and one that subtracts names the flag its area comes off (flag_id).
    """

    feature_type: FeatureType
    area_impact: AreaImpact = "marker_only"
    sketch_x: float = Field(allow_inf_nan=False)
    sketch_y: float = Field(allow_inf_nan=False)
    rotation: float = Field(default=0, allow_inf_nan=False)
    shape: Optional[FeatureShape] = None
    dimensions: Optional[dict[str, Any]] = None
    label: Optional[str] = Field(default=None, max_length=MAX_LABEL_LENGTH)
    pay_item_id: Optional[UUID] = None
    flag_id: Optional[UUID] = None
    reported_quantity: Optional[Decimal] = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check(self) -> "FeatureCreate":
        """
        Check the shape and dimensions together and against the area impact, keeping the dimensions as parsed.
        Takes the parsed feature.
        Returns it; raises ValueError naming the first rule broken.
        """
        if (self.shape is None) != (self.dimensions is None):
            raise ValueError("a feature's shape and dimensions come together")
        if self.shape is not None:
            self.dimensions = checked_dimensions(self.shape, self.dimensions, FEATURE_DIMENSIONS_BY_SHAPE)
        elif self.area_impact != "marker_only":
            raise ValueError(f"a feature that is '{self.area_impact}' needs a shape")
        if self.area_impact == "subtract" and self.flag_id is None:
            raise ValueError("a feature that subtracts names its flag (flag_id)")
        if self.label is not None:
            self.label = self.label.strip() or None
        return self


class FeatureUpdate(BaseModel):
    """
    What is changing on a feature. Any field may be left out. A null clears shape, dimensions, label, pay_item_id,
    flag_id or reported_quantity; on anything else it counts as left out. The merged record is checked as
    FeatureCreate.
    """

    feature_type: Optional[FeatureType] = None
    area_impact: Optional[AreaImpact] = None
    sketch_x: Optional[float] = None
    sketch_y: Optional[float] = None
    rotation: Optional[float] = None
    shape: Optional[FeatureShape] = None
    dimensions: Optional[dict[str, Any]] = None
    label: Optional[str] = None
    pay_item_id: Optional[UUID] = None
    flag_id: Optional[UUID] = None
    reported_quantity: Optional[Decimal] = None


class FeatureOut(BaseModel):
    """A feature: a row of icid.features, with its page's PIR, property and project. computed_quantity is its shape's area (None without one); quantity is what counts (the override, or else the area)."""

    feature_id: UUID
    pir_page_id: UUID
    pir_id: UUID
    property_id: UUID
    project_id: str
    flag_id: Optional[UUID] = None
    feature_type: str
    area_impact: str
    sketch_x: float
    sketch_y: float
    rotation: float
    shape: Optional[str] = None
    dimensions: Optional[dict[str, Any]] = None
    label: Optional[str] = None
    pay_item_id: Optional[UUID] = None
    computed_quantity: Optional[float] = None
    reported_quantity: Optional[float] = None
    quantity: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None


class FeatureResponse(BaseModel):
    status: str
    message: str
    data: FeatureOut


class FeatureListResponse(BaseModel):
    status: str
    message: str
    data: list[FeatureOut]
