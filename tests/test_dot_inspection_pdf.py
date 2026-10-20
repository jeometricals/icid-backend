"""DOT's Pedestrian Ramp Inspection Form filled from a corner: values, slots, photos, and staying fillable."""

import io
import time
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from PIL import Image
from pypdf import PdfReader

from api.services.dot_inspection_pdf import (
    ACTION_TAKEN_VALUES,
    BOROUGH_VALUES,
    CONSTRUCTION_VALUES,
    CONTRACT_TYPE_STATES,
    CORNER_DIRECTION_VALUES,
    CORNER_TYPE_STATES,
    CURB_MATERIAL_VALUES,
    DWS_ALIGNED_STATES,
    DWS_COLOR_VALUES,
    DWS_MATERIAL_VALUES,
    ENTITY_VALUES,
    RAMP_TYPE_STATES,
    SIDEWALK_MATERIAL_VALUES,
    TEMPLATE,
    TRAFFIC_CONTROL_VALUES,
    fill_inspection_forms,
    plan_copies,
)
from api.services.dot_pdf_fill import DotForm, format_number, prepare_image
from tests.dot_pdf_helpers import busy_jpeg, field_values, flattened_text, jpeg, page_image_xobjects, widgets

CORNER: dict[str, Any] = {
    "corner_id": "8f0c0d3e-0000-0000-0000-000000000001", "project_id": "HWS0023",
    "inspected_by": "Jane Inspector", "inspection_date": date(2026, 10, 8), "entity": "ddc_dot_ped_ramp",
    "entity_if_other": None, "boro": "Bronx", "dot_corner_id": "C-12345", "corner_number": "2",
    "north_south_street": "Broadway", "east_west_street": "W 42 St", "traffic_control_ns": "signal_aps",
    "traffic_control_ew": "stop_yield", "corner_type": "intersection_corner", "corner_direction": "NE",
    "number_of_ramps": 3, "t_intersection": False, "painted_roadway": True, "contractor_name": "Acme Paving",
    "permittee_id": "P-77", "fms_id": "FMS-9", "permit_number": "PN-1",
    "construction_completion_date": "2026-10-01", "contract_type": "complex", "curb_material": "steel_faced",
    "sidewalk_material": "pigmented_concrete", "comments": "Not on DOT's form",
}
PERPENDICULAR = {
    "ramp_id_label": "R1", "is_there_ponding": False, "maximum_curb_reveal_at_ramp_in": Decimal("0.25"),
    "clear_space_within_crosswalk": True, "outside_of_parallel_travel_lane": True, "dws_length_min_24_inch": True,
    "dws_full_ramp_width": False, "dws_aligned_with_ramp": "na", "dws_left_offset_from_curb_in": Decimal("2.00"),
    "dws_right_offset_from_curb_in": Decimal("1.5"), "dws_color": "dark_on_light",
    "dws_material": "cast_in_place_surface_applied", "construction": "new_install",
    "action_taken": "ada_compliant_constructed", "comments": "Looks good.", "left_flare_traversable_path": False,
    "right_flare_traversable_path": True, "ramp_running_slope_pct": Decimal("8.30"),
    "ramp_cross_slope_pct": Decimal("1.5"), "ramp_length_in": Decimal("72"), "ramp_width_in": Decimal("48.0"),
    "landing_running_slope_pct": None, "gutter_flow_slope_pct": Decimal("1.9"),
}
PARALLEL = {
    "ramp_id_label": "P1", "bottom_landing_length_in": Decimal("60"), "right_ramp_cross_slope_pct": Decimal("1.25"),
    "left_ramp_running_slope_pct": Decimal("7.5"), "action_taken": "max_extent_feasible",
    "dws_aligned_with_ramp": "yes",
}
CUT_THROUGH = {
    "ramp_id_label": "CT-A", "is_there_ponding": True, "cut_through_length_in": Decimal("120"),
    "min_clearance_between_dws_in": Decimal("48"), "ramp_width_in": Decimal("60"), "dws_color": "red",
    "action_taken": "other", "comments": "A side",
}


def ramp(property_id: str, ramp_type: str, attributes: dict[str, Any] | None = None,
         label: str | None = None) -> dict[str, Any]:
    """Build a ramp as the service takes it."""
    return {
        "property_id": property_id, "ramp_type": ramp_type, "quadrant": "NE", "facing_street": "street_1",
        "intersection_street_1": "Broadway", "intersection_street_2": "W 42 St",
        "ramp_id_label": label if label is not None else (attributes or {}).get("ramp_id_label", property_id),
        "attributes": attributes,
    }


@pytest.fixture(scope="module")
def filled() -> bytes:
    """One copy: a cut-through pair in slot 1, a perpendicular ramp in slot 2."""
    ramps = [
        ramp("p1", "PERPENDICULAR", PERPENDICULAR),
        ramp("c1", "CUT_THROUGH", CUT_THROUGH),
        ramp("c2", "CUT_THROUGH", dict(CUT_THROUGH, ramp_id_label="CT-B", comments="B side",
                                       cut_through_length_in=None)),
    ]
    result = fill_inspection_forms(CORNER, ramps, {})
    assert len(result.files) == 1
    return result.files[0]


def test_corner_page_values(filled: bytes) -> None:
    """Sections 1-3 carry the corner's values, the dropdowns their export values."""
    values = field_values(filled)
    assert values["INSPECTED_BY"] == "Jane Inspector"
    assert values["INSPECTION_DATE"] == "10/8/26"
    assert values["CONSTRUCTION_COMPLETED"] == "10/1/26"
    assert values["ENTITY"] == "DDC Managed DOT Ped Ramp Contract"
    assert values["BOROUGH"] == "Bronx"
    assert values["CORNER ID"] == "C-12345"
    assert values["NORTH-SOUTH_STREET"] == "Broadway" and values["EAST-WEST_STREET"] == "W 42 St"
    assert values["TRAFFIC_CONTROL_N-S"] == "APS" and values["TRAFFIC_CONTROL_E-W"] == "SIGN"
    assert values["CORNER_TYPE"] == "/CORNER"
    assert values["CORNER_DIR"] == "NE"
    assert values["CORNER_NUM"] == "2" and values["NUM_OF_RAMPS"] == "3"
    assert values["T_INTERSECTION"] == "/FALSE" and values["PAINTED_SPACE"] == "/TRUE"
    assert values["CONTRACT_TYPE"] == "/COMPLEX"
    assert values["CURB_MATERIAL"] == "STEEL FACED CONCRETE"
    assert values["SIDEWALK_MATERIAL"] == "PIGMENTED CONCRETE"
    assert values.get("ENTITY_OTHER") is None


def test_ramp_values_and_numbers(filled: bytes) -> None:
    """Slot 2's perpendicular ramp: numbers without trailing zeros, booleans and dropdowns by export value."""
    values = field_values(filled)
    assert values["RAMP_ID_2"] == "R1"
    assert values["RAMP_RUNNING_SLOPE_2"] == "8.3"
    assert values["RAMP_WIDTH_2"] == "48"
    assert values["RAMP_LENGTH_2"] == "72"
    assert values["RAMP_MAX_CURB_REVEAL_2"] == "0.25"
    assert values["DWS_LEFT_CORNERCURB_OFFSET_2"] == "2"
    assert values.get("LANDING_RUNNING_SLOPE_2") is None
    assert values["PONDING_2"] == "/FALSE"
    assert values["DWS_FULL_WIDTH_2"] == "/FALSE"
    assert values["CLEAR_SPACE_OUTSIDE_TRAVEL_LANE_2"] == "/TRUE"
    assert values["DWS_ALIGNED_WITH_RAMP_2"] == "/N/A"
    assert values["DWS_MATERIAL_2"] == "CAST IN PLACE SURFACE APPLIED"
    assert values["CONSTRUCTION_2"] == "New Install"
    assert values["PRP_ACTION_TAKEN_2"] == " ADA Compliant Ramp Constructed"
    assert values["RAMP_COMMENTS_2"] == "Looks good."
    assert values["STREET_NAME_1_2"] == "Broadway" and values["STREET_NAME_2_2"] == "W 42 St"


def test_cut_through_pair_on_page_10(filled: bytes) -> None:
    """The pair is CT_*_1 and CT_*_2; their shared length, clearance and comments fill the _1 fields once."""
    values = field_values(filled)
    assert values["CT_RAMP_ID_1"] == "CT-A" and values["CT_RAMP_ID_2"] == "CT-B"
    assert values["CT_PONDING_1"] == "/TRUE" and values["CT_PONDING_2"] == "/TRUE"
    assert values["CT_RAMP_LENGTH_1"] == "120"
    assert values["CT_CLEARANCE_BT_DWS_1"] == "48"
    assert values["CT_ACTION_TAKEN_1"] == "Other (Provide Explanation)"
    assert values["CT_RAMP_COMMENTS_1"] == "CT-A: A side\nCT-B: B side"
    assert values["STREET_NAME_1"] == "Broadway"
    assert values.get("RAMP_ID_1") is None and values.get("PAR_RAMP_ID_1") is None


def test_widget_states_match_values(filled: bytes) -> None:
    """A radio group shows the box its value names, and only that one."""
    states = {str(widget["/AS"]) for _, widget in widgets(filled, "CONTRACT_TYPE")}
    assert states == {"/COMPLEX", "/Off"}
    shown = {str(widget["/AS"]) for _, widget in widgets(filled, "DWS_ALIGNED_WITH_RAMP_2")}
    assert shown == {"/N/A", "/Off"}


def test_mixed_ramp_types_tick_several_boxes(filled: bytes) -> None:
    """Section 4 ticks perpendicular and cut-through, not parallel, on one radio group."""
    shown = sorted(str(widget["/AS"]) for _, widget in widgets(filled, "RAMP_TYPE"))
    assert shown == ["/CUT THROUGH", "/Off", "/PERPENDICULAR"]


def test_stays_fillable(filled: bytes) -> None:
    """The AcroForm and every field survive, with no field made read-only and nothing flattened."""
    template = PdfReader(io.BytesIO(TEMPLATE))
    reader = PdfReader(io.BytesIO(filled))
    acro_form = reader.trailer["/Root"]["/AcroForm"]
    assert len(reader.pages) == 14
    assert bool(acro_form["/NeedAppearances"]) is True
    assert set(reader.get_fields()) == set(template.get_fields())
    assert all(not int(field.get("/Ff", 0)) & 1 for field in reader.get_fields().values())
    annotations = sum(len(page.annotations or []) for page in reader.pages)
    assert annotations == sum(len(page.annotations or []) for page in template.pages)


def test_printed_text_shows(filled: bytes) -> None:
    """The fields' appearances print the values: dropdowns their display text."""
    corner_page = flattened_text(filled, 7)
    for text in ("Jane Inspector", "10/8/26", "DDC Managed DOT Ped Ramp Contract", "The Bronx", "Signal with APS"):
        assert text in corner_page
    ramp_page = flattened_text(filled, 12)
    assert "8.3" in ramp_page and "Cast in Place (surface applied)" in ramp_page


def test_enum_values_are_template_options() -> None:
    """Every ICID choice value maps to an option or state the template's field has."""
    form = DotForm(TEMPLATE)
    dropdowns = {
        "ENTITY": ENTITY_VALUES, "BOROUGH": BOROUGH_VALUES, "CORNER_DIR": CORNER_DIRECTION_VALUES,
        "TRAFFIC_CONTROL_N-S": TRAFFIC_CONTROL_VALUES, "TRAFFIC_CONTROL_E-W": TRAFFIC_CONTROL_VALUES,
        "CURB_MATERIAL": CURB_MATERIAL_VALUES, "SIDEWALK_MATERIAL": SIDEWALK_MATERIAL_VALUES,
    }
    for prefix, action in (("", "PRP_ACTION_TAKEN"), ("PAR_", "PAR_ACTION_TAKEN"), ("CT_", "CT_ACTION_TAKEN")):
        for slot in (1, 2):
            dropdowns[f"{prefix}DWS_COLOR_{slot}"] = DWS_COLOR_VALUES
            dropdowns[f"{prefix}DWS_MATERIAL_{slot}"] = DWS_MATERIAL_VALUES
            dropdowns[f"{prefix}CONSTRUCTION_{slot}"] = CONSTRUCTION_VALUES
            dropdowns[f"{action}_{slot}"] = ACTION_TAKEN_VALUES
    for field, values in dropdowns.items():
        options = form.options(field)
        assert set(values.values()) <= set(options), field
    assert form.options("BOROUGH")["Bronx"] == "The Bronx"
    radios = {"CORNER_TYPE": CORNER_TYPE_STATES, "CONTRACT_TYPE": CONTRACT_TYPE_STATES,
              "RAMP_TYPE": RAMP_TYPE_STATES, "DWS_ALIGNED_WITH_RAMP_1": DWS_ALIGNED_STATES,
              "PAR_DWS_ALIGNED_WITH_RAMP_2": DWS_ALIGNED_STATES}
    for field, states in radios.items():
        assert set(states.values()) == form.states(field), field


def test_unknown_option_is_refused() -> None:
    """A value the dropdown lacks is a programming error, not a blank."""
    with pytest.raises(ValueError):
        DotForm(TEMPLATE).choice("CURB_MATERIAL", "COBBLESTONE")


@pytest.mark.parametrize(("value", "printed"), [
    (Decimal("8.30"), "8.3"), (Decimal("48"), "48"), (48.0, "48"), (Decimal("100"), "100"),
    (Decimal("0.25"), "0.25"), (None, ""), (Decimal("-2.50"), "-2.5"), (7, "7"),
])
def test_format_number(value: Any, printed: str) -> None:
    """Numbers print without trailing zeros or exponents."""
    assert format_number(value) == printed


def test_slot_one_ramp() -> None:
    """One ramp: one copy, page 8's fields."""
    result = fill_inspection_forms(CORNER, [ramp("p1", "PERPENDICULAR", PERPENDICULAR)], {})
    values = field_values(result.files[0])
    assert len(result.files) == 1
    assert values["RAMP_ID_1"] == "R1" and values["RAMP_RUNNING_SLOPE_1"] == "8.3"
    assert values.get("RAMP_ID_2") is None


def test_slot_two_ramps_by_type() -> None:
    """A perpendicular then a parallel ramp: page 8 and page 13."""
    result = fill_inspection_forms(CORNER, [ramp("p1", "PERPENDICULAR", PERPENDICULAR),
                                            ramp("q1", "PARALLEL", PARALLEL)], {})
    values = field_values(result.files[0])
    assert len(result.files) == 1
    assert values["RAMP_ID_1"] == "R1"
    assert values["PAR_RAMP_ID_2"] == "P1"
    assert values["PAR_LANDING_LENGTH_2"] == "60"
    assert values["PAR__RIGHT_RAMP_CROSS_SLOPE_2"] == "1.25"
    assert values["PAR_ACTION_TAKEN_2"] == "Constructed to the Maximum Extent Feasible"
    assert values["PAR_DWS_ALIGNED_WITH_RAMP_2"] == "/YES"


def test_slot_three_ramps_two_files() -> None:
    """Three ramps: a second copy, with the corner page again, takes the third in slot 1."""
    ramps = [ramp(f"p{i}", "PERPENDICULAR", dict(PERPENDICULAR, ramp_id_label=f"R{i}")) for i in (1, 2, 3)]
    result = fill_inspection_forms(CORNER, ramps, {})
    assert len(result.files) == 2
    first, second = (field_values(pdf) for pdf in result.files)
    assert (first["RAMP_ID_1"], first["RAMP_ID_2"]) == ("R1", "R2")
    assert second["RAMP_ID_1"] == "R3" and second.get("RAMP_ID_2") is None
    assert second["INSPECTED_BY"] == "Jane Inspector"
    assert len(PdfReader(io.BytesIO(result.files[1])).pages) == 14


def test_slot_cut_through_pair_goes_first() -> None:
    """A perpendicular ramp listed before a cut-through pair still yields slot 1 to the pair: pages 10 and 12."""
    ramps = [ramp("p1", "PERPENDICULAR", PERPENDICULAR), ramp("c1", "CUT_THROUGH", CUT_THROUGH),
             ramp("c2", "CUT_THROUGH", CUT_THROUGH, label="CT-B")]
    result = fill_inspection_forms(CORNER, ramps, {})
    values = field_values(result.files[0])
    assert len(result.files) == 1
    assert values["CT_RAMP_ID_1"] == "CT-A" and values["CT_RAMP_ID_2"] == "CT-B"
    assert values["RAMP_ID_2"] == "R1" and values.get("RAMP_ID_1") is None


def test_slot_two_cut_through_pairs_two_files() -> None:
    """A second cut-through pair needs its own copy's slot 1."""
    ramps = [ramp(f"c{i}", "CUT_THROUGH", CUT_THROUGH, label=f"CT-{i}") for i in range(1, 5)]
    result = fill_inspection_forms(CORNER, ramps, {})
    assert len(result.files) == 2
    second = field_values(result.files[1])
    assert (second["CT_RAMP_ID_1"], second["CT_RAMP_ID_2"]) == ("CT-3", "CT-4")


def test_slot_lone_cut_through() -> None:
    """An unpaired cut-through uses page 10's _1 fields alone."""
    result = fill_inspection_forms(CORNER, [ramp("c1", "CUT_THROUGH", CUT_THROUGH)], {})
    values = field_values(result.files[0])
    assert values["CT_RAMP_ID_1"] == "CT-A" and values.get("CT_RAMP_ID_2") is None
    assert values["CT_RAMP_COMMENTS_1"] == "A side"


def test_no_ramps_and_no_attributes() -> None:
    """A corner without ramps fills its corner page; a ramp without attributes gets its ID and street names only."""
    assert plan_copies([]) == [[]]
    assert len(fill_inspection_forms(CORNER, [], {}).files) == 1
    values = field_values(fill_inspection_forms(CORNER, [ramp("p1", "PERPENDICULAR", None, label="R9")], {}).files[0])
    assert values["RAMP_ID_1"] == "R9" and values["STREET_NAME_2"] == "W 42 St"
    assert values.get("PONDING_1") is None


def test_photos_placed_and_left_out() -> None:
    """Slot 1's eight photos: the first six by number into Photo_1-1..6, captioned, two reported left out; the
    slot 2 ramp's photo goes on page 14, every widget of its field filled."""
    sizes = {n: (300 + 10 * n, 200) for n in range(1, 9)}
    photos = {
        "p1": [(n, f"view {n}", jpeg(*sizes[n])) for n in (8, 3, 1, 6, 2, 5, 4, 7)],
        "p2": [(9, None, jpeg(200, 300)), (10, "second", jpeg(200, 300))],
    }
    ramps = [ramp("p1", "PERPENDICULAR", PERPENDICULAR), ramp("p2", "PERPENDICULAR", PERPENDICULAR, "R2")]
    result = fill_inspection_forms(CORNER, ramps, photos)
    pdf = result.files[0]
    assert result.photos_left_out == 2
    for box in range(1, 7):
        [(page, widget)] = widgets(pdf, f"Photo_1-{box}")
        [image] = page_image_xobjects(widget)
        assert page == 11
        assert (int(image["/Width"]), int(image["/Height"])) == sizes[box]
        assert widget["/MK"].raw_get("/I").idnum == widget["/AP"].raw_get("/N").idnum
        assert "/BG" not in widget["/MK"]
    duplicated = widgets(pdf, "Photo_2-2")
    assert len(duplicated) == 2 and all(page_image_xobjects(widget) for _, widget in duplicated)
    assert widgets(pdf, "Photo_2-3")[0][1]["/AP"]["/N"].get_object().get("/Resources") is None or \
        "/XObject" not in widgets(pdf, "Photo_2-3")[0][1]["/AP"]["/N"].get_object().get("/Resources", {})
    photo_page = flattened_text(pdf, 11)
    assert "Photo 1: view 1" in photo_page and "Photo 6: view 6" in photo_page and "Photo 7" not in photo_page
    assert "Photo 9" in flattened_text(pdf, 14) and "Photo 10: second" in flattened_text(pdf, 14)


def test_cut_through_pair_shares_slot_one_photos() -> None:
    """Both ramps of a pair feed slot 1's boxes, ordered by number across them."""
    photos = {"c1": [(3, "a3", jpeg(100, 80)), (1, "a1", jpeg(100, 80))], "c2": [(2, "b2", jpeg(100, 80))]}
    ramps = [ramp("c1", "CUT_THROUGH", CUT_THROUGH), ramp("c2", "CUT_THROUGH", CUT_THROUGH, "CT-B")]
    result = fill_inspection_forms(CORNER, ramps, photos)
    text = flattened_text(result.files[0], 11)
    assert text.index("Photo 1: a1") < text.index("Photo 2: b2") < text.index("Photo 3: a3")
    assert result.photos_left_out == 0


def test_caption_is_cut_to_the_box() -> None:
    """A long caption is cut with an ellipsis inside the box's width."""
    photos = {"p1": [(1, "word " * 80, jpeg(100, 80))]}
    result = fill_inspection_forms(CORNER, [ramp("p1", "PERPENDICULAR", PERPENDICULAR)], photos)
    text = flattened_text(result.files[0], 11)
    assert "Photo 1: word" in text and "..." in text


def test_prepare_image_shrinks_and_turns_upright() -> None:
    """A big photo comes out at most 1600 px and well under 500 KB; EXIF orientation is applied."""
    prepared = prepare_image(busy_jpeg(4000, 3000))
    assert max(prepared.width, prepared.height) == 1600
    assert len(prepared.data) < 500_000
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90 degrees clockwise
    turned = prepare_image(jpeg(400, 200, exif=exif.tobytes()))
    assert (turned.width, turned.height) == (200, 400)
    png = io.BytesIO()
    Image.new("RGBA", (50, 40), (255, 0, 0, 0)).save(png, "PNG")
    assert Image.open(io.BytesIO(prepare_image(png.getvalue()).data)).mode == "RGB"


def test_three_ramp_corner_with_twelve_big_photos_is_fast() -> None:
    """Three ramps and twelve 4000x3000 photos fill in well under five seconds."""
    photo = busy_jpeg(4000, 3000)
    ramps = [ramp(f"p{i}", "PERPENDICULAR", PERPENDICULAR, f"R{i}") for i in (1, 2, 3)]
    photos = {f"p{i}": [(4 * (i - 1) + n, f"photo {n}", photo) for n in range(1, 5)] for i in (1, 2, 3)}
    started = time.perf_counter()
    result = fill_inspection_forms(CORNER, ramps, photos)
    elapsed = time.perf_counter() - started
    print(f"3-ramp corner, 12 photos 4000x3000: {elapsed:.2f} s, {[len(pdf) for pdf in result.files]} bytes")
    assert len(result.files) == 2 and result.photos_left_out == 0
    assert elapsed < 5.0
