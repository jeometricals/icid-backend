from datetime import date as Date
from datetime import datetime
import re
from typing import Annotated, Literal, Optional
from uuid import UUID

from pydantic import AfterValidator, BaseModel, computed_field, model_validator

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
    "sidewalk": ("boro", "block", "lot", "address", "property_type"),
    "ped_ramp": ("boro", "intersection_street_1", "intersection_street_2", "quadrant", "facing_street", "ramp_type"),
}
ONLY_BY_KIND: dict[str, tuple[str, ...]] = {
    "sidewalk": ("block", "lot", "address", "property_type", "violation_no"),
    "ped_ramp": ("intersection_street_1", "intersection_street_2", "quadrant", "facing_street", "ramp_type"),
}


def _tax_map_number(value: str, name: str, digits: int) -> str:
    """
    Read a block or a lot as the tax map numbers it: digits only, its leading zeros dropped ("0" for zero).
    Takes the value as sent, the field's name and the most digits it has without its leading zeros.
    Returns the number as text; raises ValueError for anything else.
    """
    value = value.strip()
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError(f"{name} must be digits only")
    number = value.lstrip("0") or "0"
    if len(number) > digits:
        raise ValueError(f"{name} can have at most {digits} digits")
    return number


# A sidewalk's block (at most 5 digits) and lot (at most 4), as the table's CHECKs hold them
Block = Annotated[str, AfterValidator(lambda value: _tax_map_number(value, "block", 5))]
Lot = Annotated[str, AfterValidator(lambda value: _tax_map_number(value, "lot", 4))]


class PropertyCreate(BaseModel):
    """
    A property being added to a project: a sidewalk (the default) or a ped ramp. Each kind must have its own fields
    and can't carry the other's (a 422). The route trims the text; a block and a lot lose their leading zeros.
    """

    kind: Kind = "sidewalk"
    boro: Optional[Boro] = None
    block: Optional[Block] = None
    lot: Optional[Lot] = None
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
    block: Optional[Block] = None
    lot: Optional[Lot] = None
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
    labelled (display_address).
    """

    id: UUID
    project_id: str
    kind: Kind
    boro: Boro
    block: Optional[str] = None
    lot: Optional[str] = None
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
        return display_address_of(self.kind, self.address, self.intersection_street_1, self.intersection_street_2,
                                  self.quadrant, self.facing_street)


def display_address_of(kind: str, address: Optional[str], street_1: Optional[str], street_2: Optional[str],
                       quadrant: Optional[str], facing_street: Optional[str]) -> str:
    """
    Label a property: a sidewalk by its address; a ped ramp by its intersection, quadrant and the street it faces.
    Takes its kind, address, two intersection streets, quadrant and facing_street ('street_1' or 'street_2').
    Returns the label ("" for a sidewalk without an address).
    """
    if kind == "sidewalk":
        return address or ""
    facing = street_1 if facing_street == "street_1" else street_2
    return f"{street_1} & {street_2} — {quadrant} Quadrant (Facing {facing})"


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
    task_order_id: Optional[UUID] = None
    task_order_no: Optional[str] = None


class PropertyDetail(PropertyOut):
    """One property as its own page reads it: with the id and number of the latest live task order it is in, or None."""

    task_order_id: Optional[UUID] = None
    task_order_no: Optional[str] = None


class PropertyResponse(BaseModel):
    status: str
    message: str
    data: PropertyOut


class PropertyDetailResponse(BaseModel):
    status: str
    message: str
    data: PropertyDetail


class PropertyListResponse(BaseModel):
    status: str
    message: str
    data: list[PropertyListItem]


class PropertyConflict(BaseModel):
    """The 409 body when a project already has a sidewalk at the borough, block and lot: the reason, and that property."""

    detail: str
    existing_property_id: UUID
