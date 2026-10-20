"""
NYC DOT's Pedestrian Ramp Inspection Form (version 1.6, `templates/dot_ped_ramp_inspection_v1.6.pdf`) filled from a
ped ramp corner and its ramps, left fillable. The field map is data here; `dot_pdf_fill` does the filling.

The form's fields are on pages 7 to 14: the corner (sections 1-4) on page 7; ramp slot 1 on page 8 (perpendicular),
9 (parallel) or 10 (a cut-through's two ramps) with its photos on page 11; ramp slot 2 on page 12 (perpendicular) or
13 (parallel) with its photos on page 14. A cut-through pair takes slot 1, so it goes first; the other ramps take the
next free slot in order. A corner with more ramps than one copy holds fills further copies of the whole form, each
with the corner page. Every copy keeps all 14 pages; a type page no ramp uses stays blank.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from api.services.dot_pdf_fill import (
    DotForm,
    as_date,
    draw_caption,
    format_number,
    load_template,
    prepare_image,
    template_field_names,
    warn_unmapped,
)

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "dot_ped_ramp_inspection_v1.6.pdf"
TEMPLATE = load_template(TEMPLATE_NAME)
PHOTOS_PER_SLOT = 6
CAPTION_BAND_PT = 9.0

# ICID's choice values to the form's export values
ENTITY_VALUES = {
    "ddc_city_agency_capital": "DDC City Agency Sponsored Capital Project",
    "ddc_landmark_ped_ramp": "DDC Landmark Ped Ramp Contract",
    "ddc_dot_ped_ramp": "DDC Managed DOT Ped Ramp Contract",
    "ddc_prior_notice": "DDC Prior Notice Contract",
    "ddc_ta_ped_ramp": "DDC TA Ped Ramp Contract",
    "dep": "DEP", "dob": "DOB", "dpr": "DPR", "edc": "EDC", "mta": "MTA", "nysdot": "NYSDOT", "sca": "SCA",
    "other": "Other",
}
CURB_MATERIAL_VALUES = {
    "steel_faced": "STEEL FACED CONCRETE", "concrete": "CONCRETE", "concrete_and_gutter": "CONCRETE CURB AND GUTTER",
    "granite": "GRANITE", "bluestone": "BLUESTONE", "other": "OTHER",
}
SIDEWALK_MATERIAL_VALUES = {
    "concrete": "CONCRETE", "pigmented_concrete": "PIGMENTED CONCRETE", "bluestone": "BLUESTONE",
    "cobblestone": "COBBLESTONE", "other": "OTHER",
}
TRAFFIC_CONTROL_VALUES = {"signal": "SIGNAL", "signal_aps": "APS", "stop_yield": "SIGN", "none": "NONE"}
DWS_COLOR_VALUES = {
    "red": "RED", "white": "WHITE", "dark_on_light": "DARK ON LIGHT", "light_on_dark": "LIGHT ON DARK",
    "other": "OTHER",
}
DWS_MATERIAL_VALUES = {
    "preformed_plastic_embedded": "PLASTIC DE", "preformed_plastic_surface_applied": "PLASTIC DSA",
    "cast_in_place_surface_applied": "CAST IN PLACE SURFACE APPLIED", "cast_iron": "IRON", "stone": "STONE",
    "concrete_tile": "CONCRETE",
}
CONSTRUCTION_VALUES = {
    "existing_ramp": "Existing Ramp", "new_install": "New Install", "upgrade": "Upgrade",
    "ramp_removed": "Ramp Removed",
}
ACTION_TAKEN_VALUES = {
    "ada_compliant_constructed": " ADA Compliant Ramp Constructed",  # DOT's value starts with a space
    "max_extent_feasible": "Constructed to the Maximum Extent Feasible",
    "ada_compliant_exists": "ADA Compliant Ramp Exists",
    "ramp_removed": "Ramp Removed",
    "other": "Other (Provide Explanation)",
}
BOROUGH_VALUES = {boro: boro for boro in ("Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island")}
CORNER_DIRECTION_VALUES = {direction: direction for direction in ("SW", "NW", "NE", "SE", "N", "S", "E", "W")}
# Radio states
CORNER_TYPE_STATES = {
    "intersection_corner": "/CORNER", "island_median": "/ISLAND-MEDIAN", "mid_block": "/MID BLOCK",
    "top_of_t": "/TOP TEE",
}
CONTRACT_TYPE_STATES = {"simple": "/SIMPLE", "complex": "/COMPLEX"}
RAMP_TYPE_STATES = {"PERPENDICULAR": "/PERPENDICULAR", "PARALLEL": "/PARALLEL", "CUT_THROUGH": "/CUT THROUGH"}
DWS_ALIGNED_STATES = {"yes": "/YES", "no": "/NO", "na": "/N/A"}
BOOLEAN_STATES = {True: "/TRUE", False: "/FALSE"}
STATE_MAPS = (CORNER_TYPE_STATES, CONTRACT_TYPE_STATES, DWS_ALIGNED_STATES, BOOLEAN_STATES)

# Section 1-4: (field, the corner's column, kind). Kinds: text, date, number, a value map (a dropdown), or a
# state map (a radio group, True / False included)
CORNER_FIELDS: tuple[tuple[str, str, Any], ...] = (
    ("INSPECTED_BY", "inspected_by", "text"),
    ("INSPECTION_DATE", "inspection_date", "date"),
    ("ENTITY", "entity", ENTITY_VALUES),
    ("ENTITY_OTHER", "entity_if_other", "text"),
    ("BOROUGH", "boro", BOROUGH_VALUES),
    ("CORNER ID", "dot_corner_id", "text"),
    ("NORTH-SOUTH_STREET", "north_south_street", "text"),
    ("EAST-WEST_STREET", "east_west_street", "text"),
    ("TRAFFIC_CONTROL_N-S", "traffic_control_ns", TRAFFIC_CONTROL_VALUES),
    ("TRAFFIC_CONTROL_E-W", "traffic_control_ew", TRAFFIC_CONTROL_VALUES),
    ("CORNER_TYPE", "corner_type", CORNER_TYPE_STATES),
    ("CORNER_DIR", "corner_direction", CORNER_DIRECTION_VALUES),
    ("CORNER_NUM", "corner_number", "text"),
    ("T_INTERSECTION", "t_intersection", BOOLEAN_STATES),
    ("NUM_OF_RAMPS", "number_of_ramps", "number"),
    ("PAINTED_SPACE", "painted_roadway", BOOLEAN_STATES),
    ("CONTRACTOR_NAME", "contractor_name", "text"),
    ("PERMITTEE_ID", "permittee_id", "text"),
    ("FMS_ID", "fms_id", "text"),
    ("PERMIT_NUM", "permit_number", "text"),
    ("CONSTRUCTION_COMPLETED", "construction_completion_date", "date"),
    ("CONTRACT_TYPE", "contract_type", CONTRACT_TYPE_STATES),
    ("CURB_MATERIAL", "curb_material", CURB_MATERIAL_VALUES),
    ("SIDEWALK_MATERIAL", "sidewalk_material", SIDEWALK_MATERIAL_VALUES),
)

# Section 5 of every ramp type: (field without its prefix and slot suffix, the attributes' column, kind)
_SHARED_RAMP_FIELDS: tuple[tuple[str, str, Any], ...] = (
    ("PONDING", "is_there_ponding", BOOLEAN_STATES),
    ("RAMP_MAX_CURB_REVEAL", "maximum_curb_reveal_at_ramp_in", "number"),
    ("DWS_LENGTH_24_MIN", "dws_length_min_24_inch", BOOLEAN_STATES),
    ("DWS_FULL_WIDTH", "dws_full_ramp_width", BOOLEAN_STATES),
    ("DWS_LEFT_CORNERCURB_OFFSET", "dws_left_offset_from_curb_in", "number"),
    ("DWS_RIGHT_CORNERCURB_OFFSET", "dws_right_offset_from_curb_in", "number"),
    ("DWS_COLOR", "dws_color", DWS_COLOR_VALUES),
    ("DWS_MATERIAL", "dws_material", DWS_MATERIAL_VALUES),
    ("CONSTRUCTION", "construction", CONSTRUCTION_VALUES),
    ("GUTTER_CROSS_SLOPE", "gutter_cross_slope_pct", "number"),
    ("GUTTER_FLOW_SLOPE", "gutter_flow_slope_pct", "number"),
)
PERPENDICULAR_FIELDS = _SHARED_RAMP_FIELDS + (
    ("PRP_ACTION_TAKEN", "action_taken", ACTION_TAKEN_VALUES),
    ("RAMP_COMMENTS", "comments", "text"),
    ("CLEAR_SPACE_IN_CROSSWALK", "clear_space_within_crosswalk", BOOLEAN_STATES),
    ("CLEAR_SPACE_OUTSIDE_TRAVEL_LANE", "outside_of_parallel_travel_lane", BOOLEAN_STATES),
    ("DWS_ALIGNED_WITH_RAMP", "dws_aligned_with_ramp", DWS_ALIGNED_STATES),
    ("LEFT_FLARE_TRAVERSABLE_PATH", "left_flare_traversable_path", BOOLEAN_STATES),
    ("RIGHT_FLARE_TRAVERSABLE_PATH", "right_flare_traversable_path", BOOLEAN_STATES),
    ("RAMP_RUNNING_SLOPE", "ramp_running_slope_pct", "number"),
    ("RAMP_CROSS_SLOPE", "ramp_cross_slope_pct", "number"),
    ("RAMP_LENGTH", "ramp_length_in", "number"),
    ("RAMP_WIDTH", "ramp_width_in", "number"),
    ("LANDING_RUNNING_SLOPE", "landing_running_slope_pct", "number"),
    ("LANDING_CROSS_SLOPE", "landing_cross_slope_pct", "number"),
    ("LANDING_LENGTH", "landing_length_in", "number"),
    ("LANDING_WIDTH", "landing_width_in", "number"),
    ("LEFT_FLARE_SLOPE", "left_flare_slope_pct", "number"),
    ("RIGHT_FLARE_SLOPE", "right_flare_slope_pct", "number"),
    ("LEFT_SIDEWALK_APPROACH_WIDTH", "left_sidewalk_approach_width_in", "number"),
    ("LEFT_SIDEWALK_RUNNING_SLOPE", "left_sidewalk_running_slope_pct", "number"),
    ("LEFT_SIDEWALK_CROSS_SLOPE", "left_sidewalk_cross_slope_pct", "number"),
    ("RIGHT_SIDEWALK_APPROACH_WIDTH", "right_sidewalk_approach_width_in", "number"),
    ("RIGHT_SIDEWALK_RUNNING_SLOPE", "right_sidewalk_running_slope_pct", "number"),
    ("RIGHT_SIDEWALK_CROSS_SLOPE", "right_sidewalk_cross_slope_pct", "number"),
)
PARALLEL_FIELDS = _SHARED_RAMP_FIELDS + (
    ("ACTION_TAKEN", "action_taken", ACTION_TAKEN_VALUES),
    ("RAMP_COMMENTS", "comments", "text"),
    ("CLEAR_SPACE_IN_CROSSWALK", "clear_space_within_crosswalk", BOOLEAN_STATES),
    ("DWS_ALIGNED_WITH_RAMP", "dws_aligned_with_ramp", DWS_ALIGNED_STATES),
    ("LANDING_RUNNING_SLOPE", "bottom_landing_running_slope_pct", "number"),
    ("LANDING_CROSS_SLOPE", "bottom_landing_cross_slope_pct", "number"),
    ("LANDING_LENGTH", "bottom_landing_length_in", "number"),
    ("LANDING_WIDTH", "bottom_landing_width_in", "number"),
    ("LEFT_RAMP_RUNNING_SLOPE", "left_ramp_running_slope_pct", "number"),
    ("LEFT_RAMP_CROSS_SLOPE", "left_ramp_cross_slope_pct", "number"),
    ("LEFT_RAMP_LENGTH", "left_ramp_length_in", "number"),
    ("LEFT_RAMP_WIDTH", "left_ramp_width_in", "number"),
    ("RIGHT_RAMP_RUNNING_SLOPE", "right_ramp_running_slope_pct", "number"),
    ("_RIGHT_RAMP_CROSS_SLOPE", "right_ramp_cross_slope_pct", "number"),  # DOT's name has two underscores
    ("RIGHT_RAMP_LENGTH", "right_ramp_length_in", "number"),
    ("RIGHT_RAMP_WIDTH", "right_ramp_width_in", "number"),
)
CUT_THROUGH_FIELDS = _SHARED_RAMP_FIELDS + (
    ("ACTION_TAKEN", "action_taken", ACTION_TAKEN_VALUES),
    ("RAMP_RUNNING_SLOPE", "ramp_running_slope_pct", "number"),
    ("RAMP_CROSS_SLOPE", "ramp_cross_slope_pct", "number"),
    ("RAMP_WIDTH", "ramp_width_in", "number"),
)
# A cut-through pair's one length, DWS clearance and comments box (suffix _1 only)
CUT_THROUGH_PAIR_FIELDS = (
    ("CT_RAMP_LENGTH_1", "cut_through_length_in"),
    ("CT_CLEARANCE_BT_DWS_1", "min_clearance_between_dws_in"),
)
CUT_THROUGH_COMMENTS = "CT_RAMP_COMMENTS_1"
# Per ramp type: the field prefix, its field list and the slots it has a page in
RAMP_PAGES = {
    "PERPENDICULAR": ("", PERPENDICULAR_FIELDS, (1, 2)),
    "PARALLEL": ("PAR_", PARALLEL_FIELDS, (1, 2)),
    "CUT_THROUGH": ("CT_", CUT_THROUGH_FIELDS, (1, 2)),  # one page: its two ramps are _1 and _2
}
# The ramp-location diagram's street labels, per slot
STREET_NAME_FIELDS = {1: ("STREET_NAME_1", "STREET_NAME_2"), 2: ("STREET_NAME_1_2", "STREET_NAME_2_2")}


@dataclass
class InspectionPdfs:
    """The filled forms, one PDF per copy, and how many photos didn't fit their slot's six boxes."""

    files: list[bytes]
    photos_left_out: int


@dataclass
class _Slot:
    """A ramp slot of one copy: its number (1 or 2), its ramp type and its ramp(s), two for a cut-through pair."""

    number: int
    ramp_type: str
    ramps: list[dict[str, Any]]


def plan_copies(ramps: list[dict[str, Any]]) -> list[list[_Slot]]:
    """
    Share a corner's ramps out over copies of the form: cut-throughs paired in order, each pair (or a lone one) in a
    copy's slot 1; the rest in order into the next free slot. Takes the ramps; returns each copy's slots (one copy
    with none for a corner without ramps).
    """
    cut_throughs = [ramp for ramp in ramps if ramp["ramp_type"] == "CUT_THROUGH"]
    others = [ramp for ramp in ramps if ramp["ramp_type"] != "CUT_THROUGH"]
    pairs = [cut_throughs[i:i + 2] for i in range(0, len(cut_throughs), 2)]
    copies: list[list[_Slot]] = []
    while pairs or others:
        if pairs:
            slots = [_Slot(1, "CUT_THROUGH", pairs.pop(0))]
        else:
            ramp = others.pop(0)
            slots = [_Slot(1, ramp["ramp_type"], [ramp])]
        if others:
            ramp = others.pop(0)
            slots.append(_Slot(2, ramp["ramp_type"], [ramp]))
        copies.append(slots)
    return copies or [[]]


def _fill_value(form: DotForm, field: str, value: Any, kind: Any) -> None:
    """
    Write one value into a field by its kind: text, date (m/d/yy, as the form's format script has it), number,
    a dropdown's value map or a radio group's state map. Takes the form, the field, the value and the kind.
    """
    if value is None:
        return
    if kind == "text":
        form.text(field, str(value))
    elif kind == "date":
        day = as_date(value)
        form.text(field, f"{day.month}/{day.day}/{day:%y}" if day else None)
    elif kind == "number":
        form.text(field, format_number(value))
    elif any(kind is states for states in STATE_MAPS):
        form.tick(field, [kind[value]])
    else:
        form.choice(field, kind[value])


def _fill_corner(form: DotForm, corner: dict[str, Any], ramps: list[dict[str, Any]]) -> None:
    """
    Fill page 7: sections 1-3 from the corner, section 4 ticking every ramp type the corner's ramps have.
    Takes the form, the corner and all its ramps.
    """
    for field, column, kind in CORNER_FIELDS:
        value = corner.get(column)
        if column == "number_of_ramps" and value is None:
            value = len(ramps) or None
        _fill_value(form, field, value, kind)
    types = [RAMP_TYPE_STATES[ramp_type] for ramp_type in RAMP_TYPE_STATES
             if any(ramp["ramp_type"] == ramp_type for ramp in ramps)]
    form.tick("RAMP_TYPE", types)


def _ramp_label(ramp: dict[str, Any]) -> Optional[str]:
    """
    Read a ramp's Ramp ID label, the ramp's own or else its attributes'.
    Takes the ramp; returns the label or None.
    """
    return ramp.get("ramp_id_label") or (ramp.get("attributes") or {}).get("ramp_id_label")


def _fill_slot(form: DotForm, slot: _Slot) -> None:
    """
    Fill a slot's ramp page: each ramp's section 5 (a cut-through pair's two as _1 and _2), its Ramp ID and the
    diagram's street labels. Takes the form and the slot.
    """
    prefix, fields, _ = RAMP_PAGES[slot.ramp_type]
    first = slot.ramps[0]
    for street_field, street in zip(STREET_NAME_FIELDS[slot.number],
                                    (first["intersection_street_1"], first["intersection_street_2"])):
        form.text(street_field, street)
    for index, ramp in enumerate(slot.ramps):
        suffix = f"_{index + 1}" if slot.ramp_type == "CUT_THROUGH" else f"_{slot.number}"
        form.text(f"{prefix}RAMP_ID{suffix}", _ramp_label(ramp))
        attributes = ramp.get("attributes") or {}
        for field, column, kind in fields:
            _fill_value(form, f"{prefix}{field}{suffix}", attributes.get(column), kind)
    if slot.ramp_type == "CUT_THROUGH":
        _fill_cut_through_pair(form, slot.ramps)


def _fill_cut_through_pair(form: DotForm, ramps: list[dict[str, Any]]) -> None:
    """
    Fill what a cut-through's two ramps share: one length and DWS clearance (the first ramp's, else the second's)
    and one comments box (each ramp's comments, labelled when both have some). Takes the form and the ramps.
    """
    attributes = [ramp.get("attributes") or {} for ramp in ramps]
    for field, column in CUT_THROUGH_PAIR_FIELDS:
        value = next((row[column] for row in attributes if row.get(column) is not None), None)
        form.text(field, format_number(value))
    comments = [(ramp, row["comments"]) for ramp, row in zip(ramps, attributes) if row.get("comments")]
    if len(comments) == 1:
        form.text(CUT_THROUGH_COMMENTS, comments[0][1])
    elif comments:
        form.text(CUT_THROUGH_COMMENTS, "\n".join(
            f"{_ramp_label(ramp) or f'Ramp {i + 1}'}: {text}" for i, (ramp, text) in enumerate(comments)))


def _fill_photos(form: DotForm, slot: _Slot, photos: dict[str, list[tuple[int, Optional[str], bytes]]]) -> int:
    """
    Put a slot's photos (its ramps' together, by photo number) into its six boxes, each captioned "Photo N:
    caption". Takes the form, the slot and the photos by property id; returns how many didn't fit.
    """
    taken = sorted((photo for ramp in slot.ramps for photo in photos.get(str(ramp["property_id"]), [])),
                   key=lambda photo: photo[0])
    for box, (number, caption, data) in enumerate(taken[:PHOTOS_PER_SLOT], start=1):
        field = f"Photo_{slot.number}-{box}"
        form.image(field, prepare_image(data), CAPTION_BAND_PT)
        draw_caption(form, field, f"Photo {number}: {caption}" if caption else f"Photo {number}", CAPTION_BAND_PT)
    return max(0, len(taken) - PHOTOS_PER_SLOT)


def fill_inspection_forms(corner: dict[str, Any], ramps: list[dict[str, Any]],
                          photos: dict[str, list[tuple[int, Optional[str], bytes]]]) -> InspectionPdfs:
    """
    Fill DOT's inspection form for a corner, as many copies as its ramps need, each left fillable.
    Takes the corner's row, its ramps ({property_id, ramp_type, ..., attributes}) in order and each ramp's photos
    by str(property_id) as (photo_number, caption, bytes); returns the PDFs and the count of photos left out.
    """
    files: list[bytes] = []
    left_out = 0
    for slots in plan_copies(ramps):
        form = DotForm(TEMPLATE)
        _fill_corner(form, corner, ramps)
        for slot in slots:
            _fill_slot(form, slot)
            left_out += _fill_photos(form, slot, photos)
        files.append(form.output())
    if left_out:
        logger.info("DOT inspection form: %d photo(s) beyond six a slot left out", left_out)
    return InspectionPdfs(files=files, photos_left_out=left_out)


def mapped_fields() -> set[str]:
    """
    List every field this map can fill, in both slots.
    Takes nothing; returns the fields' names.
    """
    names = {field for field, _, _ in CORNER_FIELDS} | {"RAMP_TYPE", CUT_THROUGH_COMMENTS}
    names |= {field for field, _ in CUT_THROUGH_PAIR_FIELDS}
    names |= {field for pair in STREET_NAME_FIELDS.values() for field in pair}
    for prefix, fields, slots in RAMP_PAGES.values():
        for slot in slots:
            names.add(f"{prefix}RAMP_ID_{slot}")
            names |= {f"{prefix}{field}_{slot}" for field, _, _ in fields}
    names |= {f"Photo_{slot}-{box}" for slot in (1, 2) for box in range(1, PHOTOS_PER_SLOT + 1)}
    return names


warn_unmapped(TEMPLATE_NAME, template_field_names(TEMPLATE), mapped_fields())
