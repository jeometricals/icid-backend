"""
The Construction Report (R1h): a property's as-built sheet for one approved IDR, on DOT form 5/95, as a one-page
letter-landscape PDF drawn with ReportLab; and an IDR's reports, one PDF per property it claimed flags on, in a ZIP.

What prints: the property and page 1's title block (box 1, the header line, boxes 3 to 5), the Construction Measure
(box 2, construction_measure.py), the sketch of the flags the IDR claimed with their non-assessable areas and the
features and segments around them (construction_sketch.py), the KEY, and the certification: the IDR inspector's
signature copy, dated the day the IDR was approved. A value ICID doesn't hold prints blank.
"""

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Optional
from uuid import UUID

from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from api.queries.construction_report import (
    find_report_idr,
    get_report_header,
    list_idr_report_properties,
    list_page_drawings,
    list_report_flags,
)
from api.schemas.property import display_address_of
from api.services.construction_measure import Measure, compute_measure
from api.services.construction_sketch import (
    MULTI_SHEET_MESSAGE,
    Fit,
    SketchError,
    SketchItem,
    bounds_of,
    fit_to_pane,
    north_degrees,
    sketch_items,
)
from api.services.export_common import signed_date
from api.services.field_edits import initials
from api.services.signatures import fetch_signature_file

logger = logging.getLogger(__name__)

PAGE_WIDTH, PAGE_HEIGHT = landscape(letter)  # 792 x 612 points
LEFT, RIGHT = 36.0, PAGE_WIDTH - 36.0
FONT, BOLD, ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
# The sketch pane: x, bottom y, width, height
PANE = (LEFT, 98.0, RIGHT - LEFT, 288.0)
CENT = Decimal("0.01")

# Box 4's nine reasons, in the form's order: (the code a title block stores, the label); any other reason is dropped
VIOLATION_REASONS = (
    ("work_already_done", "Work Already Done"), ("distinctive_material", "Distinctive Material"),
    ("landmark", "Landmark District"), ("subway", "Subway"), ("tree", "Trees"), ("vault", "Vault"),
    ("scaffolding", "Scaffolding"), ("other_perm_obstruction", "Other Perm. Obstruction"),
    ("other_temp_obstruction", "Other Temp. Obstruction"),
)
PROPERTY_STATUSES = (("all_defects_corrected", "All Defects Corrected"),
                     ("some_defects_corrected", "Some Defects Corrected"), ("no_work_done", "No Work Done"))
CORNER_STATUSES = (("ped_ramp_installed", "Ped Ramp Installed ____"), ("no_ped_ramp_exists", "No Ped Ramps Exists"),
                   ("existing_ped_ramp", "Existing Ped Ramp"))
CERTIFICATION = ("I certify that this survey has been updated to reflect the condition of the sidewalk found "
                 "immediately prior to replacement")


class ReportNotFound(Exception):
    """No approved IDR covers the property's flags (or the IDR claimed none); the route's 404."""


class ReportReadError(Exception):
    """The report's data couldn't be read; the route's 500."""


@dataclass
class ReportFile:
    """A file to hand back: its name and bytes."""

    filename: str
    content: bytes


# ---------------------------------------------------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------------------------------------------------

def _read(rows: Optional[Any]) -> Any:
    """
    Pass a query's result on, refusing a failed read.
    Takes what the query returned.
    Returns it; raises ReportReadError for None.
    """
    if rows is None:
        raise ReportReadError("Failed to read the Construction Report's data")
    return rows


def _signature(path: Optional[str], idr_id: UUID) -> Optional[ImageReader]:
    """
    Fetch the IDR's inspector signature copy, ready to stamp; best-effort, like the IDR export's.
    Takes the copy's path (None when the IDR has none) and the IDR's id (for the log).
    Returns the image, or None when there is none or it can't be fetched or read (logged; the line prints blank).
    """
    if not path:
        return None
    try:
        return ImageReader(io.BytesIO(fetch_signature_file(path)))
    except Exception as exc:  # noqa: BLE001 - a missing signature mustn't stop the report
        logger.error("Signature %s unavailable for the Construction Report of IDR %s: %s", path, idr_id, exc)
        return None


def file_stem(text: str) -> str:
    """
    Make text safe as a file name: anything but letters, digits, dots, dashes and underscores becomes one underscore.
    Takes the text.
    Returns the stem ("property" when nothing is left).
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "property"


def _render(property_id: UUID, idr_id: UUID) -> ReportFile:
    """
    Read one property's report for one IDR and draw it.
    Takes the property's and the IDR's ids (the IDR approved, and claiming flags there).
    Returns the PDF; raises ReportNotFound when the IDR claimed no flag there, SketchError when the sketch can't go on one sheet, and ReportReadError.
    """
    header = get_report_header(property_id, idr_id)
    flags = _read(list_report_flags(property_id, idr_id))
    if header is None or not flags:
        raise ReportNotFound("No approved IDR covers this property's flags")
    pages = sorted({flag["pir_page_id"] for flag in flags if flag["pir_page_id"]}, key=str)
    if len(pages) > 1:
        raise SketchError(MULTI_SHEET_MESSAGE)
    drawings = _read(list_page_drawings(pages)) if pages else {"features": [], "segments": []}
    items = sketch_items(flags, drawings)
    fit = fit_to_pane(items, PANE)
    measure = compute_measure(flags, {"cost_per_sf_4in": header["cost_per_sf_4in"],
                                      "cost_per_sf_7in": header["cost_per_sf_7in"]})
    signature = _signature(header["inspector_signature_path"], idr_id)
    content = draw_report(header, measure, items, fit, signature, north_degrees(header["north_bearing"]))
    address = _address(header)
    return ReportFile(f"{file_stem(address)}_construction_report.pdf", content)


def property_report(property_id: UUID, idr_id: Optional[UUID] = None) -> ReportFile:
    """
    Make a property's Construction Report: for the given IDR, or by default the most recently approved one that claimed a flag on it.
    Takes the property's id and, optionally, the IDR's.
    Returns the PDF; raises ReportNotFound when no approved IDR (or not the given one) covers the property's flags, SketchError and ReportReadError.
    """
    found = _read(find_report_idr(property_id, idr_id))
    if not found:
        raise ReportNotFound("No approved IDR covers this property's flags")
    return _render(property_id, found[0]["idr_id"])


def idr_reports(idr: dict[str, Any]) -> ReportFile:
    """
    Make an approved IDR's Construction Reports, one PDF per property it claimed flags on, in one ZIP (names made unique).
    Takes the IDR dict (idr_id, idr_number).
    Returns the ZIP; raises ReportNotFound when it claimed none, SketchError (naming the property) when one property's sketch can't go on one sheet, and ReportReadError.
    """
    properties = _read(list_idr_report_properties(idr["idr_id"]))
    if not properties:
        raise ReportNotFound("This IDR claimed no flags")
    buffer, names = io.BytesIO(), set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for prop in properties:
            try:
                report = _render(prop["id"], idr["idr_id"])
            except SketchError as exc:
                raise SketchError(f"{_address(prop)}: {exc}") from exc
            name, count = report.filename, 2
            while name in names:
                name = report.filename.replace(".pdf", f"_{count}.pdf")
                count += 1
            names.add(name)
            archive.writestr(name, report.content)
    return ReportFile(f"IDR_{file_stem(idr.get('idr_number') or str(idr['idr_id']))}_construction_reports.zip",
                      buffer.getvalue())


# ---------------------------------------------------------------------------------------------------------------------
# What the form prints, as text
# ---------------------------------------------------------------------------------------------------------------------

def _address(row: dict[str, Any]) -> str:
    """
    Label a property as the report does (its display address).
    Takes a row with the property's kind (property_kind or kind) and address fields.
    Returns the label.
    """
    return display_address_of(row.get("property_kind") or row.get("kind"), row.get("address"),
                              row.get("intersection_street_1"), row.get("intersection_street_2"), row.get("quadrant"),
                              row.get("facing_street"))


def _name(first: Optional[str], last: Optional[str]) -> str:
    """
    A person's name for the form.
    Takes the first and last name (either may be missing).
    Returns "First Last", or "" when neither is known.
    """
    return " ".join(part for part in (first, last) if part)


def iso_day(value: Optional[date | datetime]) -> str:
    """
    A date as the report prints it: ISO, a time taken as its day in New York (as the IDR export dates signatures).
    Takes a date, a time, or None.
    Returns YYYY-MM-DD, or "" for None.
    """
    if value is None:
        return ""
    return (signed_date(value) if isinstance(value, datetime) else value).isoformat()


def square_feet(value: Decimal) -> str:
    """
    Square feet for the form: two decimal places, thousands separated.
    Takes the amount.
    Returns the text, "—" for none.
    """
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):,.2f}" if value else "—"


def dollars(value: Optional[Decimal]) -> str:
    """
    Money for the form: dollars and cents.
    Takes the amount (None when there is no rate).
    Returns the text, "" for None.
    """
    return "" if value is None else f"${Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP):,.2f}"


def feet(value: float) -> str:
    """
    A length in feet for a sketch label, as short as it can be: 5', 5.5', 12.25'.
    Takes the length.
    Returns the label.
    """
    return f"{round(float(value), 2):g}'"


def dimension_label(shape: Optional[str], dimensions: Optional[dict[str, Any]]) -> str:
    """
    The size printed inside a flag or an area on the sketch.
    Takes the shape and its dimensions.
    Returns "5' x 5'" for a rectangle, "a'/b' x w'" for a trapezoid, "a' x b'" for a triangle, "r'" for an arc, "" otherwise.
    """
    if not dimensions:
        return ""
    if shape == "rectangle":
        return f"{feet(dimensions['L'])} x {feet(dimensions['W'])}"
    if shape == "trapezoid":
        return f"{feet(dimensions['a'])}/{feet(dimensions['b'])} x {feet(dimensions['w'])}"
    if shape == "triangle":
        return f"{feet(dimensions['a'])} x {feet(dimensions['b'])}"
    if shape == "arc":
        return f"r {feet(dimensions['r'])}"
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------------------------------------------------

def _text(c: Canvas, x: float, y: float, text: str, size: float = 7, font: str = FONT, width: Optional[float] = None,
          align: str = "left") -> None:
    """
    Write a line of text, shrunk (down to 4 pt) to fit a width when one is given.
    Takes the canvas, the position, the text, its size and font, the width it must fit and how it is aligned (left, centre, right).
    Returns nothing.
    """
    if width is not None:
        while size > 4 and stringWidth(text, font, size) > width:
            size -= 0.25
    c.setFont(font, size)
    {"left": c.drawString, "centre": c.drawCentredString, "right": c.drawRightString}[align](x, y, text)


def _box(c: Canvas, x: float, y: float, w: float, h: float, number: Optional[int] = None, title: str = "") -> None:
    """
    Draw one of the form's boxes, its circled number and title at its top-left.
    Takes the canvas, the box (x, bottom y, width, height), its number and title.
    Returns nothing.
    """
    c.setLineWidth(0.8)
    c.rect(x, y, w, h)
    if number is not None:
        c.circle(x + 8, y + h - 7, 4.5)
        _text(c, x + 8, y + h - 9.2, str(number), 6, BOLD, align="centre")
        _text(c, x + 16, y + h - 9.5, title, 6.5, BOLD)


def _checkbox(c: Canvas, x: float, y: float, label: str, checked: bool, width: float) -> None:
    """
    Draw a checkbox and its label, an X in it when checked.
    Takes the canvas, the box's bottom-left corner, the label, whether it is checked and the room the label has.
    Returns nothing.
    """
    c.setLineWidth(0.6)
    c.rect(x, y, 6, 6)
    if checked:
        c.line(x + 1, y + 1, x + 5, y + 5)
        c.line(x + 1, y + 5, x + 5, y + 1)
    _text(c, x + 9, y + 0.8, label, 6.5, width=width - 9)


def _chrome(c: Canvas, header: dict[str, Any]) -> None:
    """
    Draw the form's chrome: the title bar, the vertical margins and the header line.
    Takes the canvas and the report's header row.
    Returns nothing.
    """
    _text(c, PAGE_WIDTH / 2, 590, "CONSTRUCTION REPORT", 14, BOLD, align="centre")
    _text(c, LEFT, 592, "CITY PROPERTY", 8, BOLD)
    _text(c, RIGHT, 592, "Sheet 1 of 1", 8, align="right")
    for x, angle, text in ((20, 90, "55 Water Street, New York, NY 10041"),
                           (PAGE_WIDTH - 20, 270, "New York City Department of Transportation")):
        c.saveState()
        c.translate(x, PAGE_HEIGHT / 2)
        c.rotate(angle)
        _text(c, 0, 0, text, 7, align="centre")
        c.restoreState()
    fields = (("Contract No.", header.get("tb_contract_no")), ("Violation No.", header.get("tb_violation_no")),
              ("Boro", header.get("boro")))
    x = LEFT
    for label, value in fields:
        _text(c, x, 570, label, 8, BOLD)
        start = x + stringWidth(label, BOLD, 8) + 4
        c.setLineWidth(0.4)
        c.line(start, 568, start + 150, 568)
        _text(c, start + 3, 570, value or "", 8, width=146)
        x = start + 170


def _property_box(c: Canvas, header: dict[str, Any]) -> None:
    """
    Draw box 1, the property's information, and who surveyed, digitized and checked it.
    Takes the canvas and the report's header row.
    Returns nothing.
    """
    x, y, w, h = LEFT, 452.0, 264.0, 108.0
    _box(c, x, y, w, h, 1, "PROPERTY INFORMATION")
    survey = " ".join(part for part in (header.get("inspector_initials"), iso_day(header.get("inspector_sign_date")))
                      if part)
    computerized = " ".join(part for part in (_name(header.get("digitizer_first_name"),
                                                    header.get("digitizer_last_name")),
                                              iso_day(header.get("digitized_at"))) if part)
    checked = " ".join(part for part in (_name(header.get("re_first_name"), header.get("re_last_name")),
                                         iso_day(header.get("re_signed_at"))) if part)
    rows = (
        ("Property Address", _address(header)), ("Block", header.get("block") or ""), ("Lot", header.get("lot") or ""),
        ("Date Violation Notice Issued", iso_day(header.get("tb_date_notice_issued"))),
        ("Date Excavated", iso_day(header.get("tb_date_excavated"))),
        ("Date Constructed", iso_day(header.get("tb_date_constructed"))),
        ("Survey", survey), ("Computerized by", computerized), ("Checked by", checked),
    )
    _text(c, x + w - 6, y + h - 9.5, "Print Sign & Date", 5.5, ITALIC, align="right")
    row_height = (h - 16) / len(rows)
    for index, (label, value) in enumerate(rows):
        line_y = y + h - 16 - (index + 1) * row_height + 2.5
        _text(c, x + 6, line_y, label, 6.5, BOLD, width=100)
        c.setLineWidth(0.3)
        c.line(x + 110, line_y - 1.5, x + w - 6, line_y - 1.5)
        _text(c, x + 112, line_y, value, 6.5, width=w - 120)


def _measure_box(c: Canvas, measure: Measure) -> None:
    """
    Draw box 2, the Construction Measure: a row per thickness and the two estimates.
    Takes the canvas and the Measure.
    Returns nothing.
    """
    x, y, w, h = LEFT + 270, 452.0, RIGHT - LEFT - 270, 108.0
    _box(c, x, y, w, h, 2, "CONSTRUCTION MEASURE")
    columns = ("Total Sq.Ft", "Non-Assessable Sq.Ft", "Assessable Sq.Ft", "Assessable Cost/SF", "Total Cost")
    label_width = 132.0
    column_width = (w - label_width - 12) / len(columns)
    top = y + h - 26
    for index, title in enumerate(columns):
        _text(c, x + 6 + label_width + (index + 0.5) * column_width, top, title, 6, BOLD, column_width - 4, "centre")
    c.setLineWidth(0.3)
    for index, row in enumerate(measure.rows):
        line_y = top - 16 * (index + 1)
        c.line(x + 6, line_y - 4, x + w - 6, line_y - 4)
        _text(c, x + 6, line_y, row.label, 6.5, BOLD, width=label_width - 4)
        values = (square_feet(row.total_sf), square_feet(row.non_assessable_sf), square_feet(row.assessable_sf),
                  dollars(row.rate), dollars(row.cost) if row.total_sf else "—")
        for column, value in enumerate(values):
            _text(c, x + 6 + label_width + (column + 1) * column_width - 4, line_y, value, 7, width=column_width - 6,
                  align="right")
    _text(c, x + 6, y + 8, "Estimated Sq.Ft", 7, BOLD)
    _text(c, x + 80, y + 8, square_feet(measure.estimated_sf), 7.5)
    _text(c, x + w / 2, y + 8, "Estimated Cost", 7, BOLD)
    _text(c, x + w / 2 + 70, y + 8, dollars(measure.estimated_cost), 7.5)


def _status_boxes(c: Canvas, header: dict[str, Any]) -> None:
    """
    Draw boxes 3 to 5: the property's status, why the violation wasn't removed, and the corner's ped ramps.
    Takes the canvas and the report's header row.
    Returns nothing.
    """
    y, h = 392.0, 54.0
    status = header.get("tb_property_status")
    x, w = LEFT, 176.0
    _box(c, x, y, w, h, 3, "PROPERTY STATUS")
    for index, (code, label) in enumerate(PROPERTY_STATUSES):
        _checkbox(c, x + 8, y + h - 24 - index * 11, label, status == code, w - 14)

    reasons = set(header.get("tb_violation_not_removed_due_to") or [])
    x, w = LEFT + 182, 356.0
    _box(c, x, y, w, h, 4, "VIOLATION NOT REMOVED DUE TO")
    for index, (code, label) in enumerate(VIOLATION_REASONS):
        column, row = divmod(index, 3)
        _checkbox(c, x + 8 + column * (w - 12) / 3, y + h - 24 - row * 11, label, code in reasons, (w - 12) / 3 - 4)

    corner = header.get("tb_corner_property_status")
    x, w = LEFT + 544, RIGHT - LEFT - 544
    _box(c, x, y, w, h, 5, "CORNER PROPERTIES")
    for index, (code, label) in enumerate(CORNER_STATUSES):
        _checkbox(c, x + 8, y + h - 24 - index * 11, label, corner == code, w - 14)


def _path(c: Canvas, points: list[tuple[float, float]], close: bool = True) -> Any:
    """
    Make a path through some sheet points.
    Takes the canvas, the points and whether to close it.
    Returns the path.
    """
    path = c.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.close()
    return path


def _fill_pattern(c: Canvas, path: Any, points: list[tuple[float, float]], pattern: Callable[..., None]) -> None:
    """
    Fill a shape with a pattern drawn over its bounding box and clipped to it.
    Takes the canvas, the shape's path and points, and the pattern (called with the canvas and the box).
    Returns nothing.
    """
    c.saveState()
    c.clipPath(path, stroke=0, fill=0)
    pattern(c, *bounds_of(points))
    c.restoreState()


def _stipple(c: Canvas, min_x: float, min_y: float, max_x: float, max_y: float) -> None:
    """
    The 4" fill: a grid of fine dots, drawn as one path.
    Takes the canvas and the box to cover.
    Returns nothing.
    """
    c.setFillColorRGB(0.35, 0.35, 0.35)
    dots = c.beginPath()
    step, row = 3.2, 0
    y = min_y
    while y <= max_y:
        x = min_x + (step / 2 if row % 2 else 0)
        while x <= max_x:
            dots.rect(x - 0.3, y - 0.3, 0.6, 0.6)
            x += step
        y += step
        row += 1
    c.drawPath(dots, stroke=0, fill=1)


def _hatch(c: Canvas, min_x: float, min_y: float, max_x: float, max_y: float) -> None:
    """
    A non-assessable area's fill: yellow stripes.
    Takes the canvas and the box to cover.
    Returns nothing.
    """
    c.setStrokeColorRGB(0.85, 0.65, 0.0)
    c.setLineWidth(0.9)
    span = max_y - min_y
    x = min_x - span
    while x <= max_x:
        c.line(x, min_y, x + span, max_y)
        x += 3.0


def _centre(points: list[tuple[float, float]]) -> tuple[float, float]:
    """
    The middle of some points' bounding box.
    Takes the points.
    Returns (x, y).
    """
    min_x, min_y, max_x, max_y = bounds_of(points)
    return (min_x + max_x) / 2, (min_y + max_y) / 2


# Each feature type's glyph: a filled dot, a circled cross, a hatched square, or text in a small box
_GLYPH_TEXT = {"utility_cover": "U", "mail_box": "MB", "telephone": "TEL", "cellar_door": "CD", "tree_pit": "T",
               "fire_alarm": "FA", "detectable_warning": "DW", "meter": "M", "other": "?"}


def draw_glyph(c: Canvas, kind: str, x: float, y: float, size: float = 7.0) -> None:
    """
    Draw a feature's glyph, as the KEY shows it.
    Takes the canvas, the feature type (or 'non_assessable_flag', 'north'), its centre and its size.
    Returns nothing.
    """
    half = size / 2
    c.setLineWidth(0.6)
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)
    if kind == "hydrant":
        c.circle(x, y, half * 0.8, stroke=1, fill=1)
    elif kind == "pole":
        c.circle(x, y, half * 0.8)
        c.line(x - half * 0.8, y, x + half * 0.8, y)
        c.line(x, y - half * 0.8, x, y + half * 0.8)
    elif kind == "manhole":
        c.circle(x, y, half * 0.8)
        c.line(x - half * 0.6, y - half * 0.6, x + half * 0.6, y + half * 0.6)
    elif kind == "grate":
        c.rect(x - half, y - half, size, size)
        for offset in (-half / 2, 0, half / 2):
            c.line(x + offset, y - half, x + offset, y + half)
    elif kind == "sign_or_meter":
        c.circle(x, y + half * 0.4, half * 0.35, stroke=1, fill=1)
        c.line(x, y + half * 0.4, x, y - half)
    elif kind == "non_assessable_flag":
        c.line(x - half, y - half, x + half, y + half)
        c.line(x - half, y + half, x + half, y - half)
    elif kind == "north":
        draw_north(c, x, y, 0, size)
    else:
        c.setFillColorRGB(1, 1, 1)
        c.rect(x - half * 1.3, y - half, size * 1.3, size, stroke=1, fill=1)
        c.setFillColorRGB(0, 0, 0)
        _text(c, x, y - half * 0.55, _GLYPH_TEXT.get(kind, "?"), size * 0.6, BOLD, size * 1.2, "centre")


def draw_north(c: Canvas, x: float, y: float, degrees: float, size: float = 14.0) -> None:
    """
    Draw the north arrow, an arrow with an N, pointing the given way.
    Takes the canvas, its centre, degrees clockwise from straight up, and its length.
    Returns nothing.
    """
    c.saveState()
    c.translate(x, y)
    c.rotate(-degrees)
    c.setLineWidth(0.8)
    c.setFillColorRGB(0, 0, 0)
    c.line(0, -size / 2, 0, size / 2)
    c.drawPath(_path(c, [(0, size / 2), (-size / 5, size / 6), (size / 5, size / 6)]), stroke=1, fill=1)
    _text(c, 0, size / 2 + 2, "N", size * 0.45, BOLD, align="centre")
    c.restoreState()


def _draw_flag(c: Canvas, item: SketchItem, points: list[tuple[float, float]]) -> None:
    """
    Draw a flag: its outline filled by its thickness (stipple for 4" and 7" assessed as 4", white for 7"), an X over a flag surveyed non-assessable, and its size inside it.
    Takes the canvas, the item and its points on the sheet.
    Returns nothing.
    """
    flag = item.source
    path = _path(c, points)
    c.setFillColorRGB(1, 1, 1)
    c.drawPath(path, stroke=0, fill=1)
    if flag["surveyed_thickness"] in ("4in", "7in_as_4in"):
        _fill_pattern(c, path, points, _stipple)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.8)
    c.drawPath(_path(c, points), stroke=1, fill=0)
    min_x, min_y, max_x, max_y = bounds_of(points)
    if not flag["surveyed_is_assessable"]:
        c.setLineWidth(0.5)
        c.line(min_x, min_y, max_x, max_y)
        c.line(min_x, max_y, max_x, min_y)
    label = dimension_label(flag["shape"], flag["dimensions"])
    if label:
        x, y = _centre(points)
        c.setFillColorRGB(0, 0, 0)
        _text(c, x, y - 2, label, 6, BOLD, max_x - min_x - 2, "centre")


def _draw_item(c: Canvas, item: SketchItem, fit: Fit) -> None:
    """
    Draw one sketch item on the sheet: a flag, a non-assessable area (yellow stripes, its size), a feature (its outline when it has a size, cut out of its flag when it subtracts, then its glyph) or a segment (solid and thick for a curb, dashed otherwise).
    Takes the canvas, the item and the sketch's Fit.
    Returns nothing.
    """
    points = [fit.place(point) for point in item.points]
    if item.kind == "flag":
        _draw_flag(c, item, points)
    elif item.kind == "area":
        path = _path(c, points)
        _fill_pattern(c, path, points, _hatch)
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.5)
        c.drawPath(_path(c, points), stroke=1, fill=0)
        label = dimension_label(item.source.get("shape"), item.source.get("dimensions"))
        if label:
            min_x, _, max_x, _ = bounds_of(points)
            x, y = _centre(points)
            _text(c, x, y - 2, label, 5, width=max_x - min_x - 2, align="centre")
    elif item.kind == "feature":
        feature = item.source
        if feature.get("shape"):
            c.setStrokeColorRGB(0, 0, 0)
            c.setFillColorRGB(1, 1, 1)
            c.setLineWidth(0.5)
            c.drawPath(_path(c, points), stroke=1, fill=1 if feature.get("area_impact") == "subtract" else 0)
        x, y = _centre(points)
        draw_glyph(c, feature["feature_type"], x, y)
    elif item.kind == "segment":
        curb = item.source["segment_type"] != "other"
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(1.6 if curb else 0.6)
        if not curb:
            c.setDash(4, 2)
        c.drawPath(_path(c, points, close=False), stroke=1, fill=0)
        c.setDash([], 0)


def _sketch(c: Canvas, header: dict[str, Any], items: list[SketchItem], fit: Fit, north: Optional[float]) -> None:
    """
    Draw the sketch pane: segments under flags, flags under their areas, features on top; the streets round it in italics; the north arrow when the bearing is known.
    Takes the canvas, the header row, the items, their Fit and the north arrow's direction (None: no arrow).
    Returns nothing.
    """
    x, y, w, h = fit.pane
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.8)
    c.rect(x, y, w, h)
    c.saveState()
    c.clipPath(_path(c, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]), stroke=0, fill=0)
    for kind in ("segment", "flag", "area", "feature"):
        for item in items:
            if item.kind == kind:
                _draw_item(c, item, fit)
    c.restoreState()
    c.setFillColorRGB(0, 0, 0)
    if header.get("tb_frontage_street"):
        _text(c, x + w / 2, y + 5, header["tb_frontage_street"], 8, ITALIC, w - 40, "centre")
    for street, side, angle in ((header.get("tb_cross_street_left"), x + 10, 90),
                                (header.get("tb_cross_street_right"), x + w - 10, 270)):
        if street:
            c.saveState()
            c.translate(side, y + h / 2)
            c.rotate(angle)
            _text(c, 0, -3, street, 8, ITALIC, h - 40, "centre")
            c.restoreState()
    if north is not None:
        draw_north(c, x + w - 22, y + h - 24, north)


_KEY = (("utility_cover", "Utility Cover"), ("hydrant", "Hydrant"), ("sign_or_meter", "Sign or Meter"),
        ("mail_box", "Mail Box"), ("telephone", "Telephone"), ("cellar_door", "Cellar Doors"), ("pole", "Pole"),
        ("manhole", "Grating/Vault"), ("grate", "Grate"), ("tree_pit", "Tree Pit"),
        ("non_assessable_flag", "Non-Assessable Flag"), ("north", "North"))


def _key(c: Canvas) -> None:
    """
    Draw the KEY strip: each glyph and what it stands for.
    Takes the canvas.
    Returns nothing.
    """
    x, y, w, h = LEFT, 66.0, RIGHT - LEFT, 26.0
    _box(c, x, y, w, h)
    _text(c, x + 6, y + h / 2 - 2.5, "KEY", 7, BOLD)
    step = (w - 36) / len(_KEY)
    for index, (kind, label) in enumerate(_KEY):
        glyph_x = x + 34 + index * step
        draw_glyph(c, kind, glyph_x + 4, y + h / 2, 7 if kind != "north" else 10)
        _text(c, glyph_x + 12, y + h / 2 - 2.2, label, 5.5, width=step - 14)


def _certification(c: Canvas, header: dict[str, Any], signature: Optional[ImageReader]) -> None:
    """
    Draw the certification strip: the attestation, who inspected it and the IR #, and the IDR inspector's signature, initials and the day the IDR was approved.
    Takes the canvas, the header row and the signature image (None: a blank line).
    Returns nothing.
    """
    x, y, w, h = LEFT, 14.0, RIGHT - LEFT, 48.0
    _box(c, x, y, w, h)
    _text(c, x + 6, y + h - 10, "DIFFERENCES FROM PRELIMINARY INSPECTION", 7, BOLD)
    _text(c, x + 6, y + h - 20, CERTIFICATION, 6.5, ITALIC, width=w - 12)
    inspector = _name(header.get("reporter_first_name"), header.get("reporter_last_name"))
    _text(c, x + 6, y + 8, "Inspected By", 7, BOLD)
    _text(c, x + 62, y + 8, inspector, 7.5, width=170)
    _text(c, x + 250, y + 8, "IR #", 7, BOLD)
    _text(c, x + 272, y + 8, header.get("idr_number") or "", 7.5, width=60)
    line_x, line_w = x + 360, 190.0
    c.setLineWidth(0.4)
    c.line(line_x, y + 6, line_x + line_w, y + 6)
    if signature is not None:
        c.drawImage(signature, line_x, y + 7, line_w, 26, preserveAspectRatio=True, anchor="c", mask="auto")
    _text(c, line_x + line_w / 2, y + 0.5, "Print Sign Date", 5.5, ITALIC, align="centre")
    stamp = " ".join(part for part in (initials(header.get("reporter_first_name"), header.get("reporter_last_name")),
                                       iso_day(header.get("re_signed_at"))) if part)
    _text(c, x + w - 6, y + 8, stamp, 7.5, BOLD, align="right")


def draw_report(header: dict[str, Any], measure: Measure, items: list[SketchItem], fit: Fit,
                signature: Optional[ImageReader], north: Optional[float]) -> bytes:
    """
    Draw a Construction Report on one letter-landscape page.
    Takes the header row (get_report_header), the Measure, the sketch items and their Fit, the signature image (or None) and the north arrow's direction (or None).
    Returns the PDF's bytes.
    """
    buffer = io.BytesIO()
    c = Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT), pageCompression=1)
    c.setTitle(f"Construction Report — {_address(header)}")
    c.setAuthor("ICID")
    _chrome(c, header)
    _property_box(c, header)
    _measure_box(c, measure)
    _status_boxes(c, header)
    _sketch(c, header, items, fit, north)
    _key(c)
    _certification(c, header, signature)
    c.showPage()
    c.save()
    return buffer.getvalue()
