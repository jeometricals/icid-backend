"""DOT's Technical Infeasibility Form filled from a TIF: every section, photos, the signature drawn, DOT's block left."""

import io
from datetime import date, datetime, timezone
from typing import Any

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader

from api.services.dot_pdf_fill import DotForm
from api.services.dot_tif_pdf import (
    CORNER_TYPE_BOXES,
    PROJECT_CATEGORIES,
    RAMP_CONSTRUCTION_STATES,
    TEMPLATE,
    T_INTERSECTION_STATES,
    TIF_CHECK_FIELDS,
    fill_tif_form,
)
from tests.dot_pdf_helpers import dark_pixels, field_values, flattened_text, jpeg, page_image_xobjects, widgets

INTERNAL_USE = ("APPROVED", "DENIED", "RESUBMIT SEE COMMENTS BELOW", "NYC DOTDDC REPRESENTATIVE", "TITLE",
                "SIGNATURE_2", "DATE_2", "COMMENTS")
TIF: dict[str, Any] = {
    "id": "5b0c0d3e-0000-0000-0000-000000000001", "status": "approved",
    "submitted_by": "Sam Submitter", "submission_date": date(2026, 10, 2), "application_tracking_no": "APP-1",
    "sequence_no": "3", "permittee_name": "Pat Permittee", "permittee_id": "PID-9", "business_name": "Acme",
    "street_address": "1 Main St", "city": "New York", "state": "NY", "zip_code": "10001", "email": "a@b.example",
    "phone": "212-555-0100", "project_category": "state_agency", "project_category_name": "NYSDOT",
    "dot_project_id": "HWS0023", "type_of_project": "Ped ramps", "dot_corner_id": "TC-1", "dot_ramp_id": "TR-1",
    "ramp_construction": "upgrade", "reason_limited_row": True, "reason_existing_topography": False,
    "reason_existing_utilities": False, "reason_structures": False, "reason_trees_natural": False,
    "reason_nyct_structures": True, "reason_historic_features": False, "reason_other": True,
    "reason_other_text": "Other why", "why_requirements_cannot_be_achieved": "Because of the vault.",
    "element_ramp_width": False, "element_running_slope": False, "element_cross_slope": True,
    "element_flare_slopes": False, "element_dws": True, "element_landing_dimensions": False,
    "element_landing_slopes": False, "element_gutter_cross_slope": False, "element_gutter_flow_slope": False,
    "element_other": True, "element_other_text": "Other element", "investigation_confirmed": True,
    "additional_explanation": "We looked hard.", "signer_name": "Rita Engineer", "signer_ny_license_no": "PE 12345",
    "signed_at": datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc),  # 10/8 in New York
}
CORNER: dict[str, Any] = {
    "boro": "Queens", "north_south_street": "Main St", "east_west_street": "Roosevelt Ave",
    "corner_direction": "SW", "t_intersection": True, "corner_type": "top_of_t", "dot_corner_id": "C-1",
}
RAMP: dict[str, Any] = {"property_id": "p1", "ramp_type": "PERPENDICULAR", "ramp_id_label": "R1"}


def signature_png() -> bytes:
    """A transparent PNG with a dark stroke, as a signature is."""
    image = Image.new("RGBA", (600, 150), (0, 0, 0, 0))
    ImageDraw.Draw(image).line([(10, 120), (200, 20), (400, 130), (590, 30)], fill=(0, 0, 0, 255), width=10)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def filled() -> bytes:
    """The TIF with two photos (given out of order), a layout and a signature."""
    photos = [(2, "second", jpeg(800, 600, "red")), (1, "first", jpeg(600, 800, "blue"))]
    return fill_tif_form(TIF, CORNER, RAMP, photos, jpeg(1000, 1400, "green"), signature_png())


def test_sections_one_to_three(filled: bytes) -> None:
    """Submission, project and location carry the TIF's and the corner's values."""
    values = field_values(filled)
    assert values["SUBMITTED_BY"] == "Sam Submitter"
    assert values["SUBMISSION DATE"] == "10/02/2026"
    assert values["APPLICATION_NUM"] == "APP-1" and values["SEQUENCE_NUM"] == "3"
    assert values["PERMITTEE_NAME"] == "Pat Permittee" and values["PERMITTEE_ID"] == "PID-9"
    assert values["BUSINESS_NAME"] == "Acme" and values["STREET_ADDRESS"] == "1 Main St"
    assert (values["CITY"], values["STATE"], values["ZIP_CODE"]) == ("New York", "NY", "10001")
    assert values["EMAIL"] == "a@b.example" and values["PHONE"] == "212-555-0100"
    assert values["PROJECT_ENTITY"] == "/STATE AGENCY"
    assert values["STATE_AGENCY"] == "NYSDOT" and values.get("CITY_AGENCY") is None
    assert values["PROJECT_ID"] == "HWS0023" and values["PROJECT_TYPE"] == "Ped ramps"
    assert values["BOROUGH"] == "Queens"
    assert values["CORNER_ID"] == "TC-1" and values["RAMP_ID"] == "TR-1"
    assert values["NORTH-SOUTH_STREET"] == "Main St" and values["EAST-WEST_STREET"] == "Roosevelt Ave"
    assert values["CORNER_DIRECTION"] == "Southwest"
    assert values["TINTERSECTION"] == "/YES"
    assert values["MIDBLOCKTOP OF T"] == "/On"
    assert values.get("INTERSECTION CORNER") in (None, "/Off")
    assert values["RAMPCONSTRUCTION"] == "/Upgrade"


def test_sections_four_to_seven(filled: bytes) -> None:
    """Every reason and element box follows its column, boxes with only an /On state included."""
    values = field_values(filled)
    for field, column in TIF_CHECK_FIELDS:
        shown = {str(widget["/AS"]) for _, widget in widgets(filled, field)}
        assert shown == ({"/On"} if TIF[column] else {"/Off"}), field
        assert values.get(field) in (("/On",) if TIF[column] else (None, "/Off")), field
    assert values["OTHER_EXPLANATION"] == "Other why"
    assert values["EXPLANATION"] == "Because of the vault."
    assert values["OTHER_NON-ADA_EXPLAIN"] == "Other element"
    assert values["INVESTIGATION_EXPLANATION"] == "We looked hard."


def test_section_ten_and_signature(filled: bytes) -> None:
    """The signer's name twice, the licence, the New York day; the /Sig field unsigned, the image drawn over it."""
    values = field_values(filled)
    assert values["ENGINEERSARCHITECTLANDSCAPE ARCHITECTS NAME HEREBY CERTIFY"] == "Rita Engineer"
    assert values["NAME"] == "Rita Engineer"
    assert values["STATE_LICENSE_NUM"] == "PE 12345"
    assert values["DATE"] == "10/08/2026"
    assert values.get("SIGNATURE") is None
    [(page, widget)] = widgets(filled, "SIGNATURE")
    assert page == 9 and widget.get("/V") is None
    x1, y1, x2, y2 = (float(v) for v in widget["/Rect"])
    unsigned = fill_tif_form(TIF, CORNER, RAMP, [], None, None)
    assert dark_pixels(filled, 9, (x1, y1, x2, y2)) > 100
    assert dark_pixels(unsigned, 9, (x1, y1, x2, y2)) < 20


def test_internal_use_block_is_empty(filled: bytes) -> None:
    """Everything under "For internal use only" is DOT's."""
    values = field_values(filled)
    for field in INTERNAL_USE:
        assert values.get(field) in (None, "/Off", ""), field


def test_photos_and_layout(filled: bytes) -> None:
    """Photos by number into Photo_1-1.., captioned; the layout image into Design_Layout."""
    [(page, first)] = widgets(filled, "Photo_1-1")
    [image] = page_image_xobjects(first)
    assert page == 7 and (int(image["/Width"]), int(image["/Height"])) == (600, 800)
    [(_, second)] = widgets(filled, "Photo_1-2")
    assert (int(page_image_xobjects(second)[0]["/Width"])) == 800
    assert "/I" not in widgets(filled, "Photo_1-3")[0][1]["/MK"]
    [(layout_page, layout)] = widgets(filled, "Design_Layout")
    assert layout_page == 8 and int(page_image_xobjects(layout)[0]["/Height"]) == 1400
    text = flattened_text(filled, 7)
    assert "Photo 1: first" in text and "Photo 2: second" in text


def test_printed_text_and_fillable(filled: bytes) -> None:
    """The values print, and the form keeps its AcroForm and every field."""
    page_five = flattened_text(filled, 5)
    for text in ("Sam Submitter", "10/02/2026", "NYSDOT", "Roosevelt Ave", "Because of the vault."):
        assert text in page_five
    assert "Rita Engineer" in flattened_text(filled, 9)
    reader = PdfReader(io.BytesIO(filled))
    template = PdfReader(io.BytesIO(TEMPLATE))
    assert len(reader.pages) == 9
    assert set(reader.get_fields()) == set(template.get_fields())
    assert bool(reader.trailer["/Root"]["/AcroForm"]["/NeedAppearances"]) is True


def test_choice_values_are_template_states() -> None:
    """Every project category, corner type, T-intersection and construction value names a state the form has."""
    form = DotForm(TEMPLATE)
    assert {state for state, _ in PROJECT_CATEGORIES.values()} == form.states("PROJECT_ENTITY")
    assert set(T_INTERSECTION_STATES.values()) == form.states("TINTERSECTION")
    assert set(RAMP_CONSTRUCTION_STATES.values()) == form.states("RAMPCONSTRUCTION")
    for box in CORNER_TYPE_BOXES.values():
        assert form.states(box) == {"/On"}
    for _, name_field in PROJECT_CATEGORIES.values():
        assert name_field in form.fields


@pytest.mark.parametrize(("corner_type", "box"), [
    ("intersection_corner", "INTERSECTION CORNER"), ("island_median", "ISLANDMEDIAN"),
    ("mid_block", "MIDBLOCKTOP OF T"), ("top_of_t", "MIDBLOCKTOP OF T"),
])
def test_corner_type_boxes(corner_type: str, box: str) -> None:
    """Each corner type ticks its box; mid-block and top of "T" share one."""
    pdf = fill_tif_form({}, dict(CORNER, corner_type=corner_type), RAMP, [], None, None)
    ticked = {name for name in set(CORNER_TYPE_BOXES.values())
              if any(str(widget["/AS"]) == "/On" for _, widget in widgets(pdf, name))}
    assert ticked == {box}


def test_blank_tif_falls_back_to_corner_and_ramp_ids() -> None:
    """A TIF without DOT's corner or ramp ID prints the corner's and the ramp's; nothing else is filled."""
    values = field_values(fill_tif_form({}, CORNER, RAMP, [], None, None))
    assert values["CORNER_ID"] == "C-1" and values["RAMP_ID"] == "R1"
    assert values.get("SUBMITTED_BY") is None and values.get("DATE") is None
    assert values.get("PROJECT_ENTITY") in (None, "/Off")


def test_more_than_six_photos_keeps_the_first_six() -> None:
    """Seven photos: the first six by number."""
    photos = [(n, f"p{n}", jpeg(100, 80)) for n in range(7, 0, -1)]
    text = flattened_text(fill_tif_form(TIF, CORNER, RAMP, photos, None, None), 7)
    assert "Photo 6: p6" in text and "Photo 7" not in text
