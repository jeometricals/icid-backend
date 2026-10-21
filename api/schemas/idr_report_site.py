"""
A site a report imports (Slice U): a property of the IDR's project and the work done there. Its flags and curb
segments are the IDR's; the report's description can be written from its sites (the frontend does).
"""

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from api.schemas.property import display_address_of

WorkType = Literal["excavation", "concrete", "pavement_restoration", "other"]
# The longest "other" work text
MAX_WORK_TYPE_OTHER = 200


def _work(values: Any) -> Any:
    """
    Check a site's work: 'other' needs its text (trimmed, at most MAX_WORK_TYPE_OTHER), any other type takes none.
    Takes the raw body values.
    Returns them with the text trimmed (None for a type that isn't 'other'); raises ValueError otherwise.
    """
    if not isinstance(values, dict) or "work_type" not in values:
        return values
    text = values.get("work_type_other")
    text = text.strip() or None if isinstance(text, str) else text
    if values["work_type"] == "other":
        if not text:
            raise ValueError("Say what the other work is (work_type_other)")
        if len(text) > MAX_WORK_TYPE_OTHER:
            raise ValueError(f"work_type_other can be at most {MAX_WORK_TYPE_OTHER} characters")
        return {**values, "work_type_other": text}
    return {**values, "work_type_other": None}


class ReportSiteCreate(BaseModel):
    """A site being imported into a report: the property and the work done there."""

    model_config = ConfigDict(extra="forbid")

    property_id: UUID
    work_type: WorkType
    work_type_other: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _check_work(cls, values: Any) -> Any:
        """
        Check the work type and its text.
        Takes the raw body.
        Returns it cleaned; raises ValueError for 'other' without its text.
        """
        return _work(values)


class ReportSiteUpdate(BaseModel):
    """The work done at an imported site, changed: the type, and its text for 'other'."""

    model_config = ConfigDict(extra="forbid")

    work_type: WorkType
    work_type_other: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _check_work(cls, values: Any) -> Any:
        """
        Check the work type and its text.
        Takes the raw body.
        Returns it cleaned; raises ValueError for 'other' without its text.
        """
        return _work(values)


class ReportSiteOut(BaseModel):
    """An imported site: its row of icid.idr_report_sites and its property's fields, labelled as the property is."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    report_id: UUID
    idr_id: UUID
    property_id: UUID
    work_type: WorkType
    work_type_other: Optional[str] = None
    added_at: datetime
    added_by: UUID
    updated_at: Optional[datetime] = None
    updated_by: Optional[UUID] = None
    added_by_name: Optional[str] = None
    updated_by_name: Optional[str] = None
    kind: Literal["sidewalk", "ped_ramp"]
    boro: str
    address: Optional[str] = None
    block: Optional[str] = None
    lot: Optional[str] = None
    intersection_street_1: Optional[str] = None
    intersection_street_2: Optional[str] = None
    quadrant: Optional[str] = None
    facing_street: Optional[str] = None
    ramp_type: Optional[str] = None
    community_board: Optional[str] = None
    property_deleted_at: Optional[datetime] = None

    @computed_field
    @property
    def display_address(self) -> str:
        """
        Label the site as its property is labelled.
        Takes the site.
        Returns the label.
        """
        return display_address_of(self.kind, self.address, self.intersection_street_1, self.intersection_street_2,
                                  self.quadrant, self.facing_street)


class ReportSiteResponse(BaseModel):
    status: str
    message: str
    data: ReportSiteOut


class ReportSiteListResponse(BaseModel):
    status: str
    message: str
    data: list[ReportSiteOut]
