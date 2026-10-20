"""
Pedestrian ramp inspection (pedramp 2a): a corner (the DOT form's sections 1-3) and each ramp's attributes (section 5),
as NYC DOT's Pedestrian Ramp Inspection Form (version 1.6) has them. Slopes are percent, lengths, widths, offsets and
the curb reveal inches.
"""

from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from api.schemas.pir_annotation import Boro
from api.schemas.property import FacingStreet, Quadrant, RampType, display_address_of

TrafficControl = Literal["signal", "signal_aps", "stop_yield", "none"]
CornerType = Literal["intersection_corner", "island_median", "mid_block", "top_of_t"]
CornerDirection = Literal["SW", "NW", "NE", "SE", "N", "S", "E", "W"]
ContractType = Literal["simple", "complex"]
CurbMaterial = Literal["steel_faced", "concrete", "concrete_and_gutter", "granite", "bluestone", "cobblestone", "other"]
SidewalkMaterial = Literal["concrete", "pigmented_concrete", "bluestone", "cobblestone", "other"]
Entity = Literal["dob", "other"]
DwsAligned = Literal["yes", "no", "na"]
DwsColor = Literal["red", "white", "yellow", "other"]
DwsMaterial = Literal["preformed_plastic", "cast_in_place_plastic", "cast_iron", "stone", "other"]
Construction = Literal["existing_ramp", "ramp_removed", "new_install", "upgrade"]
ActionTaken = Literal["ada_compliant_constructed", "max_extent_feasible", "ada_compliant_exists", "ramp_removed", "other"]

# A corner's text fields and the longest each may be; blank is none
CORNER_TEXT = {
    "inspected_by": 200, "entity_if_other": 200, "dot_corner_id": 50, "corner_number": 50, "north_south_street": 200,
    "east_west_street": 200, "contractor_name": 200, "permittee_id": 100, "fms_id": 100, "permit_number": 100,
    "comments": 2000,
}
# The fields a corner must have
CORNER_REQUIRED = ("boro", "north_south_street", "east_west_street")


def _clean_text(values: dict[str, Any], limits: dict[str, int]) -> dict[str, Any]:
    """
    Trim the text fields of a body, blank ones to None, refusing one that is too long.
    Takes the raw values and the text fields' limits.
    Returns the values; raises ValueError naming a field over its limit.
    """
    for name, limit in limits.items():
        value = values.get(name)
        if isinstance(value, str):
            value = value.strip() or None
            if value is not None and len(value) > limit:
                raise ValueError(f"{name} can be at most {limit} characters")
            values[name] = value
    return values


class CornerFields(BaseModel):
    """A corner's fields, every one optional; the shape shared by create and update."""

    model_config = ConfigDict(extra="forbid")

    inspected_by: Optional[str] = None
    inspection_date: Optional[Date] = None
    entity: Optional[Entity] = None
    entity_if_other: Optional[str] = None
    boro: Optional[Boro] = None
    dot_corner_id: Optional[str] = None
    corner_number: Optional[str] = None
    north_south_street: Optional[str] = None
    east_west_street: Optional[str] = None
    traffic_control_ns: Optional[TrafficControl] = None
    traffic_control_ew: Optional[TrafficControl] = None
    corner_type: Optional[CornerType] = None
    corner_direction: Optional[CornerDirection] = None
    number_of_ramps: Optional[int] = Field(default=None, ge=1, le=3)
    t_intersection: Optional[bool] = None
    painted_roadway: Optional[bool] = None
    contractor_name: Optional[str] = None
    permittee_id: Optional[str] = None
    fms_id: Optional[str] = None
    permit_number: Optional[str] = None
    construction_completion_date: Optional[Date] = None
    contract_type: Optional[ContractType] = None
    curb_material: Optional[CurbMaterial] = None
    sidewalk_material: Optional[SidewalkMaterial] = None
    comments: Optional[str] = None

    @field_validator(*CORNER_TEXT, mode="before")
    @classmethod
    def _text(cls, value: Any, info: Any) -> Any:
        """
        Trim a text field, blank to None, refusing one over its limit.
        Takes the value and the field's info.
        Returns the cleaned value; raises ValueError when too long.
        """
        return _clean_text({info.field_name: value}, {info.field_name: CORNER_TEXT[info.field_name]})[info.field_name]


class CornerCreate(CornerFields):
    """A corner being added: its borough and two streets required (the form's location), every other field optional."""

    @model_validator(mode="after")
    def _required(self) -> "CornerCreate":
        """
        Refuse a corner without its borough or either street, left out, null or blank.
        Takes the validated corner.
        Returns it; raises ValueError naming the first one missing.
        """
        for name in CORNER_REQUIRED:
            if getattr(self, name) is None:
                raise ValueError(f"{name} is required")
        return self


class CornerUpdate(CornerFields):
    """What is changing on a corner: any field; a null clears an optional one (the required ones can't be cleared)."""


class CornerRamp(BaseModel):
    """A ramp (a live ped ramp property) at a corner, as the corner lists it, labelled as the property is."""

    property_id: UUID
    intersection_street_1: str
    intersection_street_2: str
    quadrant: Quadrant
    facing_street: FacingStreet
    ramp_type: RampType
    ramp_id_label: Optional[str] = None
    has_attributes: bool

    @computed_field
    @property
    def display_address(self) -> str:
        """
        Label the ramp as its property is labelled.
        Takes the ramp.
        Returns the label.
        """
        return display_address_of("ped_ramp", None, self.intersection_street_1, self.intersection_street_2,
                                  self.quadrant, self.facing_street)


class CornerOut(CornerFields):
    """A corner: a row of icid.ped_ramp_corners, with its live ramps."""

    model_config = ConfigDict(extra="ignore")

    corner_id: UUID
    project_id: str
    boro: Boro
    north_south_street: str
    east_west_street: str
    created_at: datetime
    updated_at: datetime
    created_by: UUID
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[UUID] = None
    ramps: list[CornerRamp] = []


class CornerResponse(BaseModel):
    status: str
    message: str
    data: CornerOut


class CornerListResponse(BaseModel):
    status: str
    message: str
    data: list[CornerOut]


# ---------------------------------------------------------------------------------------------------------------------
# A ped ramp site: a corner and one of its ramps, added together (Slice R)
# ---------------------------------------------------------------------------------------------------------------------

# The fields a site's corner must have: a corner's, and its direction, which finds a live corner to reuse
SITE_CORNER_REQUIRED = CORNER_REQUIRED + ("corner_direction",)
# The ramp's text fields and the longest each may be; blank is none
SITE_RAMP_TEXT = {"ramp_id_label": 50, "notes": 2000}


class PedrampSiteCreate(CornerFields):
    """
    A ped ramp site being added: the corner's fields (its borough, two streets and direction required) and the ramp's.
    The ramp takes its borough and streets from its corner: street_1 is the north-south street, street_2 the east-west.
    """

    quadrant: Quadrant
    facing_street: FacingStreet
    ramp_type: RampType
    ramp_id_label: Optional[str] = None
    notes: Optional[str] = None

    @field_validator(*SITE_RAMP_TEXT, mode="before")
    @classmethod
    def _ramp_text(cls, value: Any, info: Any) -> Any:
        """
        Trim a ramp's text field, blank to None, refusing one over its limit.
        Takes the value and the field's info.
        Returns the cleaned value; raises ValueError when too long.
        """
        return _clean_text({info.field_name: value}, {info.field_name: SITE_RAMP_TEXT[info.field_name]})[info.field_name]

    @model_validator(mode="after")
    def _required(self) -> "PedrampSiteCreate":
        """
        Refuse a site whose corner lacks its borough, either street or its direction, left out, null or blank.
        Takes the validated site.
        Returns it; raises ValueError naming the first one missing.
        """
        for name in SITE_CORNER_REQUIRED:
            if getattr(self, name) is None:
                raise ValueError(f"{name} is required")
        return self


class PedrampSiteOut(BaseModel):
    """A ped ramp site as added: its corner's id and its ramp's (the property's), and whether the corner was a live one reused."""

    corner_id: UUID
    property_id: UUID
    corner_reused: bool


class PedrampSiteResponse(BaseModel):
    status: str
    message: str
    data: PedrampSiteOut


# ---------------------------------------------------------------------------------------------------------------------
# A ramp's attributes
# ---------------------------------------------------------------------------------------------------------------------

# The fields every ramp has, then those of each ramp type, as the form's pages have them
COMMON_FIELDS = (
    "ramp_id_label", "is_there_ponding", "maximum_curb_reveal_at_ramp_in", "dws_length_min_24_inch",
    "dws_full_ramp_width", "dws_left_offset_from_curb_in", "dws_right_offset_from_curb_in", "dws_color",
    "dws_material", "construction", "action_taken", "comments",
)
TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "PERPENDICULAR": (
        "left_flare_traversable_path", "right_flare_traversable_path", "clear_space_within_crosswalk",
        "outside_of_parallel_travel_lane", "dws_aligned_with_ramp", "ramp_running_slope_pct", "ramp_cross_slope_pct",
        "ramp_length_in", "ramp_width_in", "landing_running_slope_pct", "landing_cross_slope_pct", "landing_length_in",
        "landing_width_in", "left_flare_slope_pct", "right_flare_slope_pct", "gutter_cross_slope_pct",
        "gutter_flow_slope_pct", "left_sidewalk_approach_width_in", "left_sidewalk_running_slope_pct",
        "left_sidewalk_cross_slope_pct", "right_sidewalk_approach_width_in", "right_sidewalk_running_slope_pct",
        "right_sidewalk_cross_slope_pct",
    ),
    "PARALLEL": (
        "clear_space_within_crosswalk", "dws_aligned_with_ramp", "bottom_landing_running_slope_pct",
        "bottom_landing_cross_slope_pct", "bottom_landing_length_in", "bottom_landing_width_in",
        "left_ramp_running_slope_pct", "left_ramp_cross_slope_pct", "left_ramp_length_in", "left_ramp_width_in",
        "right_ramp_running_slope_pct", "right_ramp_cross_slope_pct", "right_ramp_length_in", "right_ramp_width_in",
        "gutter_cross_slope_pct", "gutter_flow_slope_pct",
    ),
    "CUT_THROUGH": (
        "ramp_running_slope_pct", "ramp_cross_slope_pct", "ramp_width_in", "gutter_cross_slope_pct",
        "gutter_flow_slope_pct", "cut_through_length_in", "min_clearance_between_dws_in",
    ),
}
ALL_FIELDS = COMMON_FIELDS + tuple(dict.fromkeys(field for fields in TYPE_FIELDS.values() for field in fields))
ATTRIBUTE_TEXT = {"ramp_id_label": 50, "comments": 2000}

Slope = Optional[Decimal]
Inches = Optional[Decimal]


class RampAttributesIn(BaseModel):
    """
    A ramp's attributes as a PUT sends them: the whole set (a field left out is cleared). Slopes are percent (any
    finite number), inches 0 or more. Fields of another ramp type than the property's are refused by the route.
    """

    model_config = ConfigDict(extra="forbid")

    ramp_id_label: Optional[str] = None
    is_there_ponding: Optional[bool] = None
    maximum_curb_reveal_at_ramp_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    clear_space_within_crosswalk: Optional[bool] = None
    outside_of_parallel_travel_lane: Optional[bool] = None
    dws_length_min_24_inch: Optional[bool] = None
    dws_full_ramp_width: Optional[bool] = None
    dws_aligned_with_ramp: Optional[DwsAligned] = None
    dws_left_offset_from_curb_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    dws_right_offset_from_curb_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    dws_color: Optional[DwsColor] = None
    dws_material: Optional[DwsMaterial] = None
    construction: Optional[Construction] = None
    action_taken: Optional[ActionTaken] = None
    comments: Optional[str] = None
    left_flare_traversable_path: Optional[bool] = None
    right_flare_traversable_path: Optional[bool] = None
    ramp_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    ramp_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    ramp_length_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    ramp_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    landing_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    landing_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    landing_length_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    landing_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    left_flare_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    right_flare_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    gutter_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    gutter_flow_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    left_sidewalk_approach_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    left_sidewalk_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    left_sidewalk_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    right_sidewalk_approach_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    right_sidewalk_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    right_sidewalk_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    bottom_landing_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    bottom_landing_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    bottom_landing_length_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    bottom_landing_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    left_ramp_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    left_ramp_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    left_ramp_length_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    left_ramp_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    right_ramp_running_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    right_ramp_cross_slope_pct: Slope = Field(default=None, allow_inf_nan=False)
    right_ramp_length_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    right_ramp_width_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    cut_through_length_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)
    min_clearance_between_dws_in: Inches = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator(*ATTRIBUTE_TEXT, mode="before")
    @classmethod
    def _text(cls, value: Any, info: Any) -> Any:
        """
        Trim a text field, blank to None, refusing one over its limit.
        Takes the value and the field's info.
        Returns the cleaned value; raises ValueError when too long.
        """
        return _clean_text({info.field_name: value}, {info.field_name: ATTRIBUTE_TEXT[info.field_name]})[info.field_name]


class AdaWarning(BaseModel):
    """A value outside what the ADA and NYC DOT allow (the form's advisories): shown, never blocking a save."""

    field: str
    value: Any
    limit: str
    message: str


class RampAttributesOut(RampAttributesIn):
    """A ramp's attributes: its row of icid.ped_ramp_attributes, its property's ramp type, and the ADA warnings its values raise."""

    model_config = ConfigDict(extra="ignore")

    property_id: UUID
    ramp_type: RampType
    created_at: datetime
    updated_at: datetime
    warnings: list[AdaWarning] = []


class RampAttributesResponse(BaseModel):
    status: str
    message: str
    data: RampAttributesOut


class InspectionRamp(CornerRamp):
    """One ramp of a corner's inspection report: the ramp (property) and its attributes (None when not yet entered)."""

    attributes: Optional[RampAttributesOut] = None


class InspectionReport(BaseModel):
    """A corner's inspection report, structured: the corner (sections 1-4) and its ramps' attributes (section 5)."""

    corner: CornerOut
    ramp_types: list[RampType]
    ramps: list[InspectionRamp]


class InspectionReportResponse(BaseModel):
    status: str
    message: str
    data: InspectionReport


# ---------------------------------------------------------------------------------------------------------------------
# The form's wording, for the warnings and the inspection report
# ---------------------------------------------------------------------------------------------------------------------

# A corner's fields as the form names them (sections 1-3)
CORNER_LABELS = {
    "inspected_by": "Inspected by", "inspection_date": "Inspection date", "entity": "Entity",
    "entity_if_other": "Entity (if other)", "boro": "Borough", "dot_corner_id": "Corner ID",
    "north_south_street": "North-south street", "east_west_street": "East-west street",
    "traffic_control_ns": "Traffic control (north-south)", "traffic_control_ew": "Traffic control (east-west)",
    "corner_type": "Corner type", "corner_direction": "Corner direction", "corner_number": "Corner No.",
    "t_intersection": "T-intersection", "number_of_ramps": "Number of ramps at corner",
    "painted_roadway": "Painted roadway", "contractor_name": "Contractor name", "permittee_id": "Permittee ID",
    "fms_id": "FMS ID", "permit_number": "Permit number", "construction_completion_date": "Construction completion date",
    "contract_type": "Contract type", "curb_material": "Curb material", "sidewalk_material": "Sidewalk material",
    "comments": "Comments",
}
# A ramp's fields as the form names them (section 5)
FIELD_LABELS = {
    "ramp_id_label": "Ramp ID", "is_there_ponding": "Is there ponding",
    "maximum_curb_reveal_at_ramp_in": "Maximum curb reveal at ramp",
    "clear_space_within_crosswalk": "Clear space within crosswalk",
    "outside_of_parallel_travel_lane": "Outside of parallel travel lane",
    "dws_length_min_24_inch": "DWS length min. 24 inch", "dws_full_ramp_width": "DWS full ramp width",
    "dws_aligned_with_ramp": "DWS aligned with ramp", "dws_left_offset_from_curb_in": "DWS left offset from curb",
    "dws_right_offset_from_curb_in": "DWS right offset from curb", "dws_color": "DWS color",
    "dws_material": "DWS material", "construction": "Construction", "action_taken": "Action taken",
    "comments": "Comments", "left_flare_traversable_path": "Left flare in traversable path",
    "right_flare_traversable_path": "Right flare in traversable path", "ramp_running_slope_pct": "Ramp running slope",
    "ramp_cross_slope_pct": "Ramp cross slope", "ramp_length_in": "Ramp length", "ramp_width_in": "Ramp width",
    "landing_running_slope_pct": "Landing running slope", "landing_cross_slope_pct": "Landing cross slope",
    "landing_length_in": "Landing length", "landing_width_in": "Landing width",
    "left_flare_slope_pct": "Left flare slope", "right_flare_slope_pct": "Right flare slope",
    "gutter_cross_slope_pct": "Gutter cross slope", "gutter_flow_slope_pct": "Gutter flow slope",
    "left_sidewalk_approach_width_in": "Left sidewalk approach width",
    "left_sidewalk_running_slope_pct": "Left sidewalk running slope",
    "left_sidewalk_cross_slope_pct": "Left sidewalk cross slope",
    "right_sidewalk_approach_width_in": "Right sidewalk approach width",
    "right_sidewalk_running_slope_pct": "Right sidewalk running slope",
    "right_sidewalk_cross_slope_pct": "Right sidewalk cross slope",
    "bottom_landing_running_slope_pct": "Bottom landing running slope",
    "bottom_landing_cross_slope_pct": "Bottom landing cross slope", "bottom_landing_length_in": "Bottom landing length",
    "bottom_landing_width_in": "Bottom landing width", "left_ramp_running_slope_pct": "Left ramp running slope",
    "left_ramp_cross_slope_pct": "Left ramp cross slope", "left_ramp_length_in": "Left ramp length",
    "left_ramp_width_in": "Left ramp width", "right_ramp_running_slope_pct": "Right ramp running slope",
    "right_ramp_cross_slope_pct": "Right ramp cross slope", "right_ramp_length_in": "Right ramp length",
    "right_ramp_width_in": "Right ramp width", "cut_through_length_in": "Cut-through length",
    "min_clearance_between_dws_in": "Min. clearance between DWS",
}
# The choices as the form words them
CHOICE_LABELS = {
    "dob": "DOB", "other": "Other", "signal": "Signal", "signal_aps": "Signal with APS", "stop_yield": "Stop / yield",
    "none": "None", "intersection_corner": "Intersection corner", "island_median": "Island / median",
    "mid_block": "Mid-block", "top_of_t": "Top of \"T\"", "simple": "Simple", "complex": "Complex",
    "steel_faced": "Steel faced", "concrete": "Concrete", "concrete_and_gutter": "Concrete and gutter",
    "granite": "Granite", "bluestone": "Bluestone", "cobblestone": "Cobblestone",
    "pigmented_concrete": "Pigmented concrete", "yes": "Yes", "no": "No", "na": "N/A", "red": "Red", "white": "White",
    "yellow": "Yellow", "preformed_plastic": "Preformed plastic", "cast_in_place_plastic": "Cast-in-place plastic",
    "cast_iron": "Cast iron", "stone": "Stone", "existing_ramp": "Existing ramp", "ramp_removed": "Ramp removed",
    "new_install": "New install", "upgrade": "Upgrade", "ada_compliant_constructed": "ADA compliant ramp constructed",
    "max_extent_feasible": "Constructed to the maximum extent feasible",
    "ada_compliant_exists": "ADA compliant ramp exists", "PERPENDICULAR": "Perpendicular", "PARALLEL": "Parallel",
    "CUT_THROUGH": "Cut-through",
}
