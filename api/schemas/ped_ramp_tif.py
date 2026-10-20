"""
A Technical Infeasibility Form (Pedramp Phase 3, S3): NYC DOT's Pedestrian Ramp Technical Infeasibility /
Structurally Impractical Form, version 1.9, for one non-compliant ramp of an inspection. Drafted by an inspector or
the RE; signed and approved by the RE, who gives their name and New York licence number then.
"""

from datetime import date as Date
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from api.schemas.ped_ramp import clean_text_fields

ProjectCategory = Literal["city_agency", "state_agency", "transit_authority", "other"]
RampConstruction = Literal["new_installation", "upgrade"]

# A TIF's text fields and the longest each may be; blank is none
TIF_TEXT = {
    "submitted_by": 200, "application_tracking_no": 100, "sequence_no": 100, "permittee_name": 200,
    "permittee_id": 100, "business_name": 200, "street_address": 200, "city": 100, "state": 50, "zip_code": 20,
    "email": 200, "phone": 50, "project_category_name": 200, "dot_project_id": 100, "type_of_project": 200,
    "dot_corner_id": 50, "dot_ramp_id": 50, "reason_other_text": 500, "why_requirements_cannot_be_achieved": 4000,
    "element_other_text": 500, "additional_explanation": 4000,
}
# Its tick boxes (never null: a box is ticked or not)
TIF_CHECKS = (
    "reason_limited_row", "reason_existing_topography", "reason_existing_utilities", "reason_structures",
    "reason_trees_natural", "reason_nyct_structures", "reason_historic_features", "reason_other",
    "element_ramp_width", "element_running_slope", "element_cross_slope", "element_flare_slopes", "element_dws",
    "element_landing_dimensions", "element_landing_slopes", "element_gutter_cross_slope", "element_gutter_flow_slope",
    "element_other", "investigation_confirmed",
)


class TifFields(BaseModel):
    """What an inspector or the RE fills in on a TIF (sections 1-7), every field optional."""

    model_config = ConfigDict(extra="forbid")

    submitted_by: Optional[str] = None
    submission_date: Optional[Date] = None
    application_tracking_no: Optional[str] = None
    sequence_no: Optional[str] = None
    permittee_name: Optional[str] = None
    permittee_id: Optional[str] = None
    business_name: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    project_category: Optional[ProjectCategory] = None
    project_category_name: Optional[str] = None
    dot_project_id: Optional[str] = None
    type_of_project: Optional[str] = None
    dot_corner_id: Optional[str] = None
    dot_ramp_id: Optional[str] = None
    ramp_construction: Optional[RampConstruction] = None
    reason_limited_row: bool = False
    reason_existing_topography: bool = False
    reason_existing_utilities: bool = False
    reason_structures: bool = False
    reason_trees_natural: bool = False
    reason_nyct_structures: bool = False
    reason_historic_features: bool = False
    reason_other: bool = False
    reason_other_text: Optional[str] = None
    why_requirements_cannot_be_achieved: Optional[str] = None
    element_ramp_width: bool = False
    element_running_slope: bool = False
    element_cross_slope: bool = False
    element_flare_slopes: bool = False
    element_dws: bool = False
    element_landing_dimensions: bool = False
    element_landing_slopes: bool = False
    element_gutter_cross_slope: bool = False
    element_gutter_flow_slope: bool = False
    element_other: bool = False
    element_other_text: Optional[str] = None
    investigation_confirmed: bool = False
    additional_explanation: Optional[str] = None

    @field_validator(*TIF_TEXT, mode="before")
    @classmethod
    def _text(cls, value: Any, info: Any) -> Any:
        """
        Trim a text field, blank to None, refusing one over its limit.
        Takes the value and the field's info.
        Returns the cleaned value; raises ValueError when too long.
        """
        return clean_text_fields({info.field_name: value}, {info.field_name: TIF_TEXT[info.field_name]})[info.field_name]

    @field_validator(*TIF_CHECKS, mode="before")
    @classmethod
    def _tick(cls, value: Any) -> Any:
        """
        Take a null tick box as unticked.
        Takes the value.
        Returns False for None, else the value.
        """
        return False if value is None else value


class TifUpdate(TifFields):
    """What is changing on a draft TIF: any field; a null clears a text or a choice and unticks a box."""


class TifApprove(BaseModel):
    """The RE's signing of a TIF (section 10): their name as it prints, and their New York licence number."""

    model_config = ConfigDict(extra="forbid")

    signer_name: str = Field(max_length=200)
    signer_ny_license_no: str = Field(max_length=50)

    @field_validator("signer_name", "signer_ny_license_no")
    @classmethod
    def _not_blank(cls, value: str, info: Any) -> str:
        """
        Trim a signing field and refuse a blank one.
        Takes the value and the field's info.
        Returns it trimmed; raises ValueError when blank.
        """
        value = value.strip()
        if not value:
            raise ValueError(f"{info.field_name} is required")
        return value


class TifOut(TifFields):
    """A TIF: a row of icid.ped_ramp_tifs, with its ramp, its project and corner, its inspection's status and who signed it."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    property_id: UUID
    inspection_id: UUID
    project_id: str
    corner_id: UUID
    inspection_status: str
    ramp_type: str
    quadrant: str
    ramp_id_label: Optional[str] = None
    status: Literal["draft", "approved"]
    signer_name: Optional[str] = None
    signer_ny_license_no: Optional[str] = None
    signer_signature_path: Optional[str] = Field(default=None, exclude=True)
    signed_at: Optional[datetime] = None
    signed_by_uuid: Optional[UUID] = None
    signed_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID

    @computed_field
    @property
    def signed(self) -> bool:
        """
        Say whether the RE's signature was copied to the TIF at approval.
        Takes the TIF.
        Returns True when it was.
        """
        return self.signer_signature_path is not None


class TifResponse(BaseModel):
    status: str
    message: str
    data: TifOut


class TifListResponse(BaseModel):
    status: str
    message: str
    data: list[TifOut]
