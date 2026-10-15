from datetime import date as Date
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, computed_field, model_validator

from api.schemas.pir_annotation import Boro

# What a property can be; the table's CHECK holds the same three
PropertyType = Literal["1_3_family", "4_plus_family", "expedited"]
# The two kinds of property, and a ped ramp's own values; the database's enum types hold the same
Kind = Literal["sidewalk", "ped_ramp"]
Quadrant = Literal["NE", "NW", "SE", "SW"]
FacingStreet = Literal["street_1", "street_2"]
RampType = Literal["PERPENDICULAR", "PARALLEL", "CUT_THROUGH"]

# The fields each kind must have, and the ones only it takes
REQUIRED_BY_KIND: dict[str, tuple[str, ...]] = {
    "sidewalk": ("bbl", "address", "property_type"),
    "ped_ramp": ("boro", "intersection_street_1", "intersection_street_2", "quadrant", "facing_street", "ramp_type"),
}
ONLY_BY_KIND: dict[str, tuple[str, ...]] = {
    "sidewalk": ("bbl", "address", "property_type", "violation_no"),
    "ped_ramp": ("intersection_street_1", "intersection_street_2", "quadrant", "facing_street", "ramp_type"),
}
# A BBL's first digit, and the borough it names
BOROUGH_BY_BBL_DIGIT: dict[str, str] = {
    "1": "Manhattan", "2": "Bronx", "3": "Brooklyn", "4": "Queens", "5": "Staten Island",
}


class PropertyCreate(BaseModel):
    """
    A property being added to a project: a sidewalk (the default) or a ped ramp. Each kind must have its own fields
    and can't carry the other's (a 422). The route trims the text; a sidewalk's borough is read off its BBL.
    """

    kind: Kind = "sidewalk"
    boro: Optional[Boro] = None
    bbl: Optional[str] = None
    address: Optional[str] = None
    property_type: Optional[PropertyType] = None
    violation_no: Optional[str] = None
    intersection_street_1: Optional[str] = None
    intersection_street_2: Optional[str] = None
    quadrant: Optional[Quadrant] = None
    facing_street: Optional[FacingStreet] = None
    ramp_type: Optional[RampType] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _fields_of_its_kind(self) -> "PropertyCreate":
        """
        Check the property has every field its kind needs and none of the other kind's.
        Takes the parsed property.
        Returns it; raises ValueError naming the fields missing or out of place.
        """
        missing = [field for field in REQUIRED_BY_KIND[self.kind] if getattr(self, field) is None]
        if missing:
            raise ValueError(f"a {self.kind} property needs {', '.join(missing)}")
        other = "ped_ramp" if self.kind == "sidewalk" else "sidewalk"
        stray = [field for field in ONLY_BY_KIND[other] if getattr(self, field) is not None]
        if stray:
            raise ValueError(f"a {self.kind} property doesn't take {', '.join(stray)}")
        return self


class PropertyUpdate(BaseModel):
    """
    What is being changed on a property. Any field may be left out; a null counts as left out, except for notes and
    violation_no, where a null or a blank clears them. kind can't change: sending another kind is a 400.
    """

    kind: Optional[Kind] = None
    boro: Optional[Boro] = None
    bbl: Optional[str] = None
    address: Optional[str] = None
    property_type: Optional[PropertyType] = None
    violation_no: Optional[str] = None
    intersection_street_1: Optional[str] = None
    intersection_street_2: Optional[str] = None
    quadrant: Optional[Quadrant] = None
    facing_street: Optional[FacingStreet] = None
    ramp_type: Optional[RampType] = None
    notes: Optional[str] = None


class PropertyOut(BaseModel):
    """
    A property: a row of icid.properties, deleted_at and deleted_by None for one that isn't deleted, with how it is
    labelled (display_address) and, for a sidewalk, its block and lot as read off the BBL.
    """

    id: UUID
    project_id: str
    kind: Kind
    boro: Boro
    bbl: Optional[str] = None
    address: Optional[str] = None
    property_type: Optional[str] = None
    violation_no: Optional[str] = None
    intersection_street_1: Optional[str] = None
    intersection_street_2: Optional[str] = None
    quadrant: Optional[Quadrant] = None
    facing_street: Optional[FacingStreet] = None
    ramp_type: Optional[RampType] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None

    @computed_field
    @property
    def display_address(self) -> str:
        """
        Label the property: a sidewalk by its address; a ped ramp by its intersection, quadrant and the street it faces.
        Takes the property.
        Returns the label.
        """
        if self.kind == "sidewalk":
            return self.address or ""
        facing = self.intersection_street_1 if self.facing_street == "street_1" else self.intersection_street_2
        return (
            f"{self.intersection_street_1} & {self.intersection_street_2} — {self.quadrant} Quadrant (Facing {facing})"
        )

    @computed_field
    @property
    def block_display(self) -> Optional[str]:
        """
        Read a sidewalk's block off its BBL (digits 2-6), without leading zeros.
        Takes the property.
        Returns the block, or None for a ped ramp or a BBL that isn't 10 digits.
        """
        return _bbl_part(self.bbl, 1, 6) if self.kind == "sidewalk" else None

    @computed_field
    @property
    def lot_display(self) -> Optional[str]:
        """
        Read a sidewalk's lot off its BBL (digits 7-10), without leading zeros.
        Takes the property.
        Returns the lot, or None for a ped ramp or a BBL that isn't 10 digits.
        """
        return _bbl_part(self.bbl, 6, 10) if self.kind == "sidewalk" else None


def _bbl_part(bbl: Optional[str], start: int, end: int) -> Optional[str]:
    """
    Cut a part out of a 10-digit BBL and drop its leading zeros.
    Takes the BBL and the part's start and end (Python slice positions).
    Returns the part ("0" when it is all zeros), or None when the BBL isn't 10 digits.
    """
    if bbl is None or len(bbl) != 10 or not bbl.isdigit():
        return None
    return bbl[start:end].lstrip("0") or "0"


class PropertyStatus(BaseModel):
    """
    How far a property has got: none, in a task order, or constructed (its live PIR signed as built), with the day
    it was signed in New York.
    """

    state: Literal["none", "task_ordered", "constructed_on"]
    date: Optional[Date] = None


class PropertyListItem(PropertyOut):
    """
    A property in a project's list, with how far its live PIR has got (none, signed by DOT, or signed as built too),
    its status, and the number of the latest live task order it is in.
    """

    pir_status: Literal["none", "dot_signed", "as_built_signed"]
    status: PropertyStatus
    task_order_no: Optional[str] = None


class PropertyResponse(BaseModel):
    status: str
    message: str
    data: PropertyOut


class PropertyListResponse(BaseModel):
    status: str
    message: str
    data: list[PropertyListItem]


class PropertyConflict(BaseModel):
    """The 409 body when a project already has a property with the BBL: the reason, and that property."""

    detail: str
    existing_property_id: UUID
