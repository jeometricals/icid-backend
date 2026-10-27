from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Boro = Literal["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]
PropertyStatus = Literal["all_defects_corrected", "some_defects_corrected", "no_work_done"]
CornerPropertyStatus = Literal["ped_ramp_installed", "no_ped_ramp_exists", "existing_ped_ramp"]
MAX_TEXT_LENGTH = 200
_TEXT_FIELDS = (
    "contract_no", "violation_no", "property_address", "block", "lot", "cross_street_left", "cross_street_right",
    "frontage_street",
)
# A width in feet: zero or more
Width = Field(default=None, ge=0, allow_inf_nan=False)


class PirAnnotationIn(BaseModel):
    """A page's title block, as a PUT sets it: a field left out keeps its value, a null clears it. Text is trimmed, and blank is none."""

    contract_no: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    violation_no: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    boro: Optional[Boro] = None
    property_address: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    block: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    lot: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    cross_street_left: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    cross_street_right: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    frontage_street: Optional[str] = Field(default=None, max_length=MAX_TEXT_LENGTH)
    tax_map_width_ft: Optional[Decimal] = Width
    cc_curb_to_curb_ft: Optional[Decimal] = Width
    property_status: Optional[PropertyStatus] = None
    corner_property_status: Optional[CornerPropertyStatus] = None
    violation_not_removed_due_to: Optional[list[str]] = []
    date_notice_issued: Optional[date] = None
    date_excavated: Optional[date] = None
    date_constructed: Optional[date] = None

    @field_validator(*_TEXT_FIELDS, mode="before")
    @classmethod
    def _trimmed(cls, value: Any) -> Any:
        """
        Trim a text field, blank being none.
        Takes the value as sent.
        Returns the trimmed text, None for blank, or the value as it was when it isn't text.
        """
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("violation_not_removed_due_to")
    @classmethod
    def _reasons(cls, value: Optional[list[str]]) -> list[str]:
        """
        Trim each reason the violation wasn't removed, dropping blank ones; a null is none.
        Takes the reasons as sent.
        Returns them trimmed; raises ValueError for one over 200 characters.
        """
        reasons = [reason.strip() for reason in value or [] if reason.strip()]
        if any(len(reason) > MAX_TEXT_LENGTH for reason in reasons):
            raise ValueError(f"each reason can be at most {MAX_TEXT_LENGTH} characters")
        return reasons


class PirAnnotationOut(PirAnnotationIn):
    """A page's title block: a row of icid.pir_annotations, its widths as JSON numbers."""

    tax_map_width_ft: Optional[float] = None
    cc_curb_to_curb_ft: Optional[float] = None
    # Where tax_map_width_ft came from (Slice Y1): mappluto_<version> or manual; set by the server, never sent
    tax_map_source: Optional[str] = None
    tax_map_fetched_at: Optional[datetime] = None
    # The page's sidewalk strip (Slice Y1.5): [{x, y}, ...] in native pixels, the property line first; set through
    # PUT /v1/pir-pages/{id}/sidewalk-strip, never here
    sidewalk_strip_points: Optional[list[dict[str, float]]] = None
    sidewalk_strip_source: Optional[str] = None
    sidewalk_strip_set_at: Optional[datetime] = None
    sidewalk_strip_set_by: Optional[UUID] = None
    pir_page_id: UUID
    created_at: datetime
    updated_at: datetime


class PirAnnotationResponse(BaseModel):
    """A page's title block, or None when it has none yet."""

    status: str
    message: str
    data: Optional[PirAnnotationOut] = None
