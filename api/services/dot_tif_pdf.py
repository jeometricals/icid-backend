"""
NYC DOT's Technical Infeasibility Form (version 1.9, `templates/dot_tif_v1.9.pdf`) filled from a ped ramp TIF, its
corner and its ramp, left fillable. The field map is data here; `dot_pdf_fill` does the filling.

The fields are on pages 5 to 9: sections 1-5 on page 5, 6-7 on page 6, the photos on page 7, the design layout on
page 8 and the statement (section 10) on page 9. The signature field is a cryptographic one (/Sig): it is never set;
the RE's signature image is drawn over it as page content. The "For internal use only" block is DOT's and never filled.
"""

import io
import logging
from datetime import datetime
from typing import Any, Optional

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from api.services.dot_pdf_fill import (
    DotForm,
    as_date,
    draw_caption,
    letterbox,
    load_template,
    prepare_image,
    template_field_names,
    warn_unmapped,
)
from api.services.export_common import signed_date

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "dot_tif_v1.9.pdf"
TEMPLATE = load_template(TEMPLATE_NAME)
PHOTO_BOXES = 6
CAPTION_BAND_PT = 9.0
SIGNATURE_FIELD = "SIGNATURE"
LAYOUT_FIELD = "Design_Layout"

# Section 1 and the text of sections 2, 4-7 and 10: (field, the TIF's column)
TIF_TEXT_FIELDS = (
    ("SUBMITTED_BY", "submitted_by"),
    ("APPLICATION_NUM", "application_tracking_no"),
    ("SEQUENCE_NUM", "sequence_no"),
    ("PERMITTEE_NAME", "permittee_name"),
    ("PERMITTEE_ID", "permittee_id"),
    ("BUSINESS_NAME", "business_name"),
    ("STREET_ADDRESS", "street_address"),
    ("CITY", "city"),
    ("STATE", "state"),
    ("ZIP_CODE", "zip_code"),
    ("EMAIL", "email"),
    ("PHONE", "phone"),
    ("PROJECT_ID", "dot_project_id"),
    ("PROJECT_TYPE", "type_of_project"),
    ("OTHER_EXPLANATION", "reason_other_text"),
    ("EXPLANATION", "why_requirements_cannot_be_achieved"),
    ("OTHER_NON-ADA_EXPLAIN", "element_other_text"),
    ("INVESTIGATION_EXPLANATION", "additional_explanation"),
    ("ENGINEERSARCHITECTLANDSCAPE ARCHITECTS NAME HEREBY CERTIFY", "signer_name"),
    ("NAME", "signer_name"),
    ("STATE_LICENSE_NUM", "signer_ny_license_no"),
)
# The form's dates, mm/dd/yyyy as their format scripts have them
TIF_DATE_FIELDS = (("SUBMISSION DATE", "submission_date"),)
SIGNED_DATE_FIELD = "DATE"
# Check boxes of sections 4, 6 and 7: (field, the TIF's column). Two field names are DOT's own spelling.
TIF_CHECK_FIELDS = (
    ("LIMITED RIGHTOFWAY", "reason_limited_row"),
    ("EXISTING TOPOGRAPHY", "reason_existing_topography"),
    ("EXISTING UTILITIES", "reason_existing_utilities"),
    ("STRUCTURES BUILDINGS VAULTS BRIDGES", "reason_structures"),
    ("TREES NATURAL FEATURES", "reason_trees_natural"),
    ("NYCT  STRUCTURES ENTRANCES VENTS", "reason_nyct_structures"),
    ("HISTORIC FEATURES", "reason_historic_features"),
    ("OTHER_2", "reason_other"),
    ("RAMP WIDTH", "element_ramp_width"),
    ("RAMP RUNNING SLOPE", "element_running_slope"),
    ("RAMP CROSS SLOPE", "element_cross_slope"),
    ("FLARE SLOPES", "element_flare_slopes"),
    ("DETECTABLE WARNING SURFACE", "element_dws"),
    ("LANDING DIMENSIONS", "element_landing_dimensions"),
    ("LANDING SLOPES", "element_landing_slopes"),
    ("GUTTER CROSS SLOPE", "element_gutter_cross_slope"),
    ("GUTTER FLOW SLOPE", "element_gutter_flow_slope"),
    ("OTHER_3", "element_other"),
    ("INVESTIGATION_CONFIRMED", "investigation_confirmed"),
)
# Section 2: the project's category as PROJECT_ENTITY's state, and the text field its name goes in
PROJECT_CATEGORIES = {
    "city_agency": ("/CITY AGENCY", "CITY_AGENCY"),
    "state_agency": ("/STATE AGENCY", "STATE_AGENCY"),
    "transit_authority": ("/MTA AUTHORITY", "MTA"),
    "other": ("/OTHER ENTITY", "OTHER_ENTITY"),
}
# Section 3: (field, the corner's column)
CORNER_TEXT_FIELDS = (
    ("BOROUGH", "boro"),
    ("NORTH-SOUTH_STREET", "north_south_street"),
    ("EAST-WEST_STREET", "east_west_street"),
)
CORNER_DIRECTION_NAMES = {
    "SW": "Southwest", "NW": "Northwest", "NE": "Northeast", "SE": "Southeast",
    "N": "North", "S": "South", "E": "East", "W": "West",
}
# Corner types as check boxes; DOT's form has one box for mid-block and top of "T"
CORNER_TYPE_BOXES = {
    "intersection_corner": "INTERSECTION CORNER",
    "island_median": "ISLANDMEDIAN",
    "mid_block": "MIDBLOCKTOP OF T",
    "top_of_t": "MIDBLOCKTOP OF T",
}
T_INTERSECTION_STATES = {True: "/YES", False: "/NO"}
RAMP_CONSTRUCTION_STATES = {"new_installation": "/New Installation", "upgrade": "/Upgrade"}


def _mm_dd_yyyy(value: Any) -> Optional[str]:
    """
    Print a day as the TIF's date fields format it, mm/dd/yyyy.
    Takes a date, datetime or ISO text; returns the text, or None for none.
    """
    day = as_date(value)
    return f"{day:%m/%d/%Y}" if day else None


def _fill_sections(form: DotForm, tif: dict[str, Any], corner: dict[str, Any], ramp: dict[str, Any]) -> None:
    """
    Fill sections 1-7 and 10's text: the TIF's own fields, the corner's location, ticks and radio choices.
    Takes the form, the TIF, its corner and its ramp.
    """
    for field, column in TIF_TEXT_FIELDS:
        value = tif.get(column)
        form.text(field, str(value) if value is not None else None)
    for field, column in TIF_DATE_FIELDS:
        form.text(field, _mm_dd_yyyy(tif.get(column)))
    signed_at = tif.get("signed_at")
    if signed_at:
        moment = signed_at if isinstance(signed_at, datetime) else datetime.fromisoformat(str(signed_at))
        form.text(SIGNED_DATE_FIELD, _mm_dd_yyyy(signed_date(moment)))
    for field, column in TIF_CHECK_FIELDS:
        form.check(field, tif.get(column))
    category = tif.get("project_category")
    if category:
        state, name_field = PROJECT_CATEGORIES[category]
        form.tick("PROJECT_ENTITY", [state])
        form.text(name_field, tif.get("project_category_name"))
    for field, column in CORNER_TEXT_FIELDS:
        form.text(field, corner.get(column))
    form.text("CORNER_ID", tif.get("dot_corner_id") or corner.get("dot_corner_id"))
    form.text("RAMP_ID", tif.get("dot_ramp_id") or ramp.get("ramp_id_label"))
    form.text("CORNER_DIRECTION", CORNER_DIRECTION_NAMES.get(corner.get("corner_direction") or ""))
    if corner.get("t_intersection") is not None:
        form.tick("TINTERSECTION", [T_INTERSECTION_STATES[corner["t_intersection"]]])
    if corner.get("corner_type"):
        form.check(CORNER_TYPE_BOXES[corner["corner_type"]], True)
    if tif.get("ramp_construction"):
        form.tick("RAMPCONSTRUCTION", [RAMP_CONSTRUCTION_STATES[tif["ramp_construction"]]])


def _fill_photos(form: DotForm, photos: list[tuple[int, Optional[str], bytes]],
                 layout: Optional[bytes]) -> None:
    """
    Put the TIF's photos (by photo number, the first six) into page 7's boxes, captioned, and its design layout
    into page 8's. Takes the form, the photos as (photo_number, caption, bytes) and the layout's bytes or None.
    """
    taken = sorted(photos, key=lambda photo: photo[0])
    for box, (number, caption, data) in enumerate(taken[:PHOTO_BOXES], start=1):
        field = f"Photo_1-{box}"
        form.image(field, prepare_image(data), CAPTION_BAND_PT)
        draw_caption(form, field, f"Photo {number}: {caption}" if caption else f"Photo {number}", CAPTION_BAND_PT)
    if len(taken) > PHOTO_BOXES:
        logger.info("DOT TIF: %d photo(s) beyond six left out", len(taken) - PHOTO_BOXES)
    if layout:
        form.image(LAYOUT_FIELD, prepare_image(layout))


def _draw_signature(form: DotForm, signature_png: bytes) -> None:
    """
    Draw the signer's signature image over the signature field, letterboxed, as page content; the field itself
    stays unsigned. Takes the form and the PNG's bytes.
    """
    image = ImageReader(io.BytesIO(signature_png))
    width, height = image.getSize()
    for page_index, (x1, y1, x2, y2) in form.widget_rects(SIGNATURE_FIELD):
        x, y, w, h = letterbox((x1 + 2.0, y1 + 2.0, x2 - x1 - 4.0, y2 - y1 - 4.0), width, height)

        def paint(canvas: Canvas, box: tuple[float, float, float, float] = (x, y, w, h)) -> None:
            canvas.drawImage(image, box[0], box[1], box[2], box[3], mask="auto")

        form.draw(page_index, paint)


def fill_tif_form(tif: dict[str, Any], corner: dict[str, Any], ramp: dict[str, Any],
                  photos: list[tuple[int, Optional[str], bytes]], layout: Optional[bytes],
                  signature_png: Optional[bytes]) -> bytes:
    """
    Fill DOT's Technical Infeasibility Form for one ramp, left fillable.
    Takes the TIF's row, its corner's, its ramp ({property_id, ramp_type, ..., ramp_id_label}), its photos as
    (photo_number, caption, bytes), its design layout image and the signer's signature PNG; returns the PDF's bytes.
    """
    form = DotForm(TEMPLATE)
    _fill_sections(form, tif, corner, ramp)
    _fill_photos(form, photos, layout)
    if signature_png:
        _draw_signature(form, signature_png)
    return form.output()


def mapped_fields() -> set[str]:
    """
    List every field this map can fill.
    Takes nothing; returns the fields' names.
    """
    names = {field for field, _ in TIF_TEXT_FIELDS + TIF_DATE_FIELDS + TIF_CHECK_FIELDS}
    names |= {field for field, _ in CORNER_TEXT_FIELDS} | set(CORNER_TYPE_BOXES.values())
    names |= {name_field for _, name_field in PROJECT_CATEGORIES.values()}
    names |= {"PROJECT_ENTITY", "CORNER_ID", "RAMP_ID", "CORNER_DIRECTION", "TINTERSECTION", "RAMPCONSTRUCTION",
              SIGNED_DATE_FIELD, LAYOUT_FIELD}
    names |= {f"Photo_1-{box}" for box in range(1, PHOTO_BOXES + 1)}
    return names


warn_unmapped(TEMPLATE_NAME, template_field_names(TEMPLATE), mapped_fields())
