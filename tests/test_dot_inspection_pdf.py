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
    is_measured,
    plan_copies,
)
from api.services.dot_pdf_fill import DotForm, format_number, prepare_image
from tests.dot_pdf_helpers import (
    busy_jpeg,
    field_values,
    flattened_text,
    jpeg,
    page_count,
    page_image_xobjects,
    widgets,
)

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
    """The kept pages (corner, page 10, page 12) keep their widgets and fields, none read-only, nothing flattened."""
    template = PdfReader(io.BytesIO(TEMPLATE))
    reader = PdfReader(io.BytesIO(filled))
    acro_form = reader.trailer["/Root"]["/AcroForm"]
    assert len(reader.pages) == 3
    assert bool(acro_form["/NeedAppearances"]) is True
    assert set(reader.get_fields()) == _field_names_on(template, (7, 10, 12))
    assert all(not int(field.get("/Ff", 0)) & 1 for field in reader.get_fields().values())
    annotations = sum(len(page.annotations or []) for page in reader.pages)
    assert annotations == sum(len(template.pages[number - 1].annotations or []) for number in (7, 10, 12))


def _field_names_on(reader: PdfReader, page_numbers: tuple[int, ...]) -> set[str]:
    """List the names of the fields with a widget on the given pages (1 first)."""
    names = set()
    for number in page_numbers:
        for ref in reader.pages[number - 1].annotations or []:
            widget = ref.get_object()
            if widget.get("/Subtype") == "/Widget":
                names.add(str((widget if "/T" in widget else widget["/Parent"].get_object())["/T"]))
    return names


def test_trimmed_acro_form_has_no_dangling_fields(filled: bytes) -> None:
    """Every field left in the AcroForm (each of its kids) has its widget on a page of the file, and that widget's
    /P is a kept page; the removed pages' fields are gone."""
    reader = PdfReader(io.BytesIO(filled))
    on_pages = {ref.idnum for page in reader.pages for ref in page.annotations or []}
    kept_pages = {page.indirect_reference.idnum for page in reader.pages}

    def widgets_of(ref: Any) -> list[Any]:
        field = ref.get_object()
        return [w for kid in field["/Kids"] for w in widgets_of(kid)] if "/Kids" in field else [ref]

    fields = reader.trailer["/Root"]["/AcroForm"]["/Fields"]
    assert fields
    for ref in fields:
        for widget in widgets_of(ref):
            assert widget.idnum in on_pages
            page = widget.get_object().raw_get("/P") if "/P" in widget.get_object() else None
            if page is not None:
                assert page.idnum in kept_pages
    names = set(reader.get_fields())
    for gone in ("RAMP_ID_1", "PAR_RAMP_ID_1", "PAR_RAMP_ID_2", "Photo_1-1", "Photo_2-1"):
        assert gone not in names
    assert {"CT_RAMP_ID_1", "RAMP_ID_2", "INSPECTED_BY"} <= names


def test_trimmed_pages_render_and_start_with_the_corner(filled: bytes) -> None:
    """PDFium opens every kept page; the first is the corner page (section 1), not DOT's instructions."""
    assert "1. INS" in flattened_text(filled, 1)
    for number in (2, 3):
        assert flattened_text(filled, number).strip()


def test_printed_text_shows(filled: bytes) -> None:
    """The fields' appearances print the values: dropdowns their display text."""
    corner_page = flattened_text(filled, 1)
    for text in ("Jane Inspector", "10/8/26", "DDC Managed DOT Ped Ramp Contract", "The Bronx", "Signal with APS"):
        assert text in corner_page
    ramp_page = flattened_text(filled, 3)
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
    assert second["RAMP_ID_1"] == "R3" and "RAMP_ID_2" not in second
    assert second["INSPECTED_BY"] == "Jane Inspector"
    assert [page_count(pdf) for pdf in result.files] == [3, 2]


def test_slot_cut_through_pair_goes_first() -> None:
    """A perpendicular ramp listed before a cut-through pair still yields slot 1 to the pair: pages 10 and 12."""
    ramps = [ramp("p1", "PERPENDICULAR", PERPENDICULAR), ramp("c1", "CUT_THROUGH", CUT_THROUGH),
             ramp("c2", "CUT_THROUGH", CUT_THROUGH, label="CT-B")]
    result = fill_inspection_forms(CORNER, ramps, {})
    values = field_values(result.files[0])
    assert len(result.files) == 1
    assert values["CT_RAMP_ID_1"] == "CT-A" and values["CT_RAMP_ID_2"] == "CT-B"
    assert values["RAMP_ID_2"] == "R1" and "RAMP_ID_1" not in values


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
    assert page_count(result.files[0]) == 2


def test_no_ramps_and_no_attributes() -> None:
    """A corner without ramps is one copy of its corner page; so is one whose only ramp has no attributes."""
    assert plan_copies([]) == [[]]
    files = fill_inspection_forms(CORNER, [], {}).files
    assert len(files) == 1 and page_count(files[0]) == 1
    files = fill_inspection_forms(CORNER, [ramp("p1", "PERPENDICULAR", None, label="R9")], {}).files
    assert len(files) == 1 and page_count(files[0]) == 1
    values = field_values(files[0])
    assert "RAMP_ID_1" not in values and values["NUM_OF_RAMPS"] == "3"


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
        assert page == 3
        assert (int(image["/Width"]), int(image["/Height"])) == sizes[box]
        assert widget["/MK"].raw_get("/I").idnum == widget["/AP"].raw_get("/N").idnum
        assert "/BG" not in widget["/MK"]
    duplicated = widgets(pdf, "Photo_2-2")
    assert len(duplicated) == 2 and all(page_image_xobjects(widget) for _, widget in duplicated)
    assert widgets(pdf, "Photo_2-3")[0][1]["/AP"]["/N"].get_object().get("/Resources") is None or \
        "/XObject" not in widgets(pdf, "Photo_2-3")[0][1]["/AP"]["/N"].get_object().get("/Resources", {})
    photo_page = flattened_text(pdf, 3)
    assert "Photo 1: view 1" in photo_page and "Photo 6: view 6" in photo_page and "Photo 7" not in photo_page
    assert "Photo 9" in flattened_text(pdf, 5) and "Photo 10: second" in flattened_text(pdf, 5)
    assert page_count(pdf) == 5


def test_cut_through_pair_shares_slot_one_photos() -> None:
    """Both ramps of a pair feed slot 1's boxes, ordered by number across them."""
    photos = {"c1": [(3, "a3", jpeg(100, 80)), (1, "a1", jpeg(100, 80))], "c2": [(2, "b2", jpeg(100, 80))]}
    ramps = [ramp("c1", "CUT_THROUGH", CUT_THROUGH), ramp("c2", "CUT_THROUGH", CUT_THROUGH, "CT-B")]
    result = fill_inspection_forms(CORNER, ramps, photos)
    text = flattened_text(result.files[0], 3)
    assert text.index("Photo 1: a1") < text.index("Photo 2: b2") < text.index("Photo 3: a3")
    assert result.photos_left_out == 0


def test_caption_is_cut_to_the_box() -> None:
    """A long caption is cut with an ellipsis inside the box's width."""
    photos = {"p1": [(1, "word " * 80, jpeg(100, 80))]}
    result = fill_inspection_forms(CORNER, [ramp("p1", "PERPENDICULAR", PERPENDICULAR)], photos)
    text = flattened_text(result.files[0], 3)
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


# ---------------------------------------------------------------------------
# T3: each copy trimmed to the corner page, its slots' ramp-type pages and their photo pages
# ---------------------------------------------------------------------------

UNMEASURED = {"ramp_id_label": "U1", "is_there_ponding": True, "dws_color": "red", "comments": "Not measured",
              "construction": "new_install", "ramp_running_slope_pct": None}


def test_is_measured() -> None:
    """A *_pct or *_in value makes a ramp measured; yes / no answers, choices, the label and comments don't."""
    assert not is_measured(ramp("u", "PERPENDICULAR", UNMEASURED))
    assert not is_measured(ramp("u", "PERPENDICULAR", None))
    assert is_measured(ramp("m", "PERPENDICULAR", {"maximum_curb_reveal_at_ramp_in": Decimal("0.25")}))
    assert is_measured(ramp("m", "PARALLEL", {"left_ramp_running_slope_pct": Decimal("0")}))


@pytest.mark.parametrize(("ramps", "photo_ids", "pages"), [
    ([("p1", "PERPENDICULAR")], [], [2]),
    ([("p1", "PERPENDICULAR")], ["p1"], [3]),
    ([("p1", "PERPENDICULAR"), ("p2", "PARALLEL")], ["p2"], [4]),
    ([("p1", "PERPENDICULAR"), ("p2", "PARALLEL")], ["p1", "p2"], [5]),
    ([("p1", "PERPENDICULAR"), ("p2", "PERPENDICULAR"), ("p3", "PARALLEL")], ["p3"], [3, 3]),
])
def test_page_count_is_corner_slots_and_photo_pages(ramps: list[tuple[str, str]], photo_ids: list[str],
                                                    pages: list[int]) -> None:
    """A copy is 1 + its used slots + its used slots with photos."""
    attributes = {"PERPENDICULAR": PERPENDICULAR, "PARALLEL": PARALLEL}
    photos = {pid: [(1, "x", jpeg(60, 40))] for pid in photo_ids}
    result = fill_inspection_forms(CORNER, [ramp(pid, kind, attributes[kind]) for pid, kind in ramps], photos)
    assert [page_count(pdf) for pdf in result.files] == pages


def test_unmeasured_ramp_with_photos_is_not_printed() -> None:
    """An unmeasured ramp takes no slot and its photos don't print (nor count as left out): the corner page alone."""
    photos = {"u1": [(n, "x", jpeg(60, 40)) for n in range(1, 9)]}
    result = fill_inspection_forms(CORNER, [ramp("u1", "PERPENDICULAR", UNMEASURED)], photos)
    assert len(result.files) == 1 and page_count(result.files[0]) == 1
    assert result.photos_left_out == 0
    values = field_values(result.files[0])
    assert not any(name.startswith(("RAMP_ID", "Photo_")) for name in values)
    assert values["RAMP_TYPE"] == "/PERPENDICULAR"  # section 4 still lists every ramp of the corner


def test_unmeasured_ramp_gives_up_its_slot() -> None:
    """Three ramps, the first unmeasured: one copy, the two measured ramps in slots 1 and 2."""
    ramps = [ramp("u1", "PERPENDICULAR", UNMEASURED), ramp("p1", "PERPENDICULAR", PERPENDICULAR),
             ramp("q1", "PARALLEL", PARALLEL)]
    result = fill_inspection_forms(CORNER, ramps, {"u1": [(1, "x", jpeg(60, 40))]})
    assert len(result.files) == 1 and page_count(result.files[0]) == 3
    values = field_values(result.files[0])
    assert values["RAMP_ID_1"] == "R1" and values["PAR_RAMP_ID_2"] == "P1"
    assert "Photo_1-1" not in values and "Photo_2-1" not in values


def test_three_measured_ramps_still_two_copies() -> None:
    """Three measured ramps need two copies; the second is the corner and slot 1."""
    ramps = [ramp(f"p{i}", "PERPENDICULAR", dict(PERPENDICULAR, ramp_id_label=f"R{i}")) for i in (1, 2, 3)]
    result = fill_inspection_forms(CORNER, ramps, {})
    assert [page_count(pdf) for pdf in result.files] == [3, 2]
    assert "1. INS" in flattened_text(result.files[1], 1)


def test_cut_through_pair_with_one_side_measured() -> None:
    """A pair prints when either side is measured: page 10 kept, the unmeasured side's CT_*_2 fields blank (its
    label, answers and photos too); a pair with neither side measured is left out."""
    side_b = ramp("c2", "CUT_THROUGH", dict(UNMEASURED, ramp_id_label="CT-B", comments="B side"))
    photos = {"c2": [(1, "b", jpeg(60, 40))]}
    result = fill_inspection_forms(CORNER, [ramp("c1", "CUT_THROUGH", CUT_THROUGH), side_b], photos)
    pdf = result.files[0]
    assert len(result.files) == 1 and page_count(pdf) == 2
    values = field_values(pdf)
    assert values["CT_RAMP_ID_1"] == "CT-A" and values["CT_RAMP_LENGTH_1"] == "120"
    assert values["CT_RAMP_COMMENTS_1"] == "A side"
    side_b_values = {name: value for name, value in values.items() if name.startswith("CT_") and name.endswith("_2")}
    # " " is DOT's own default for a dropdown left untouched
    assert side_b_values and all(value in (None, "", " ", "/Off") for value in side_b_values.values())
    neither = [ramp("c1", "CUT_THROUGH", UNMEASURED), ramp("c2", "CUT_THROUGH", UNMEASURED)]
    assert [page_count(f) for f in fill_inspection_forms(CORNER, neither, {}).files] == [1]


@pytest.mark.parametrize(("slot_1", "slot_2", "kept", "gone"), [
    ("PERPENDICULAR", "PARALLEL", ("RAMP_ID_1", "PAR_RAMP_ID_2"), ("PAR_RAMP_ID_1", "CT_RAMP_ID_1", "RAMP_ID_2")),
    ("PARALLEL", "PERPENDICULAR", ("PAR_RAMP_ID_1", "RAMP_ID_2"), ("RAMP_ID_1", "CT_RAMP_ID_1", "PAR_RAMP_ID_2")),
    ("PARALLEL", "PARALLEL", ("PAR_RAMP_ID_1", "PAR_RAMP_ID_2"), ("RAMP_ID_1", "RAMP_ID_2", "CT_RAMP_ID_1")),
])
def test_mixed_types_keep_the_matching_variant_page(slot_1: str, slot_2: str, kept: tuple[str, ...],
                                                    gone: tuple[str, ...]) -> None:
    """Each slot keeps only the page of its ramp's type."""
    attributes = {"PERPENDICULAR": PERPENDICULAR, "PARALLEL": PARALLEL}
    result = fill_inspection_forms(CORNER, [ramp("a", slot_1, attributes[slot_1]),
                                            ramp("b", slot_2, attributes[slot_2])], {})
    names = set(field_values(result.files[0]))
    assert page_count(result.files[0]) == 3
    assert set(kept) <= names and not set(gone) & names
