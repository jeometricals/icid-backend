"""
A ped ramp corner's inspection report (pedramp 2a): ICID's own rendering of NYC DOT's Pedestrian Ramp Inspection Form,
as a letter-portrait PDF drawn with ReportLab. Not DOT's fillable form (that is 2b).

Page 1 is the corner: the form's sections 1 to 3 (inspection, location, construction), section 4 (the corner's ramp
types, ticked from its ramps) and its ramps listed. Then a page per ramp, its section 5 laid out as the form lays out
its ramp type, a cut-through's two ramps side by side on one page as the form has them. A value outside the form's
advisories prints in red with a mark, and the page ends with its warnings. A value not entered prints blank.
"""

import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from api.queries.ped_ramp_attributes import list_corner_ramps
from api.schemas.ped_ramp import (
    CHOICE_LABELS,
    COMMON_FIELDS,
    CORNER_LABELS,
    FIELD_LABELS,
    TYPE_FIELDS,
    AdaWarning,
    CornerOut,
    InspectionRamp,
    InspectionReport,
    RampAttributesOut,
)
from api.services.ped_ramp_ada import ada_warnings

FONT = "Helvetica"
BOLD = "Helvetica-Bold"
RED = Color(0.78, 0.08, 0.08)
GREY = Color(0.45, 0.45, 0.45)
RULE = Color(0.75, 0.75, 0.75)
PAGE_W, PAGE_H = letter
MARGIN = 40.0
LINE = 13.0
# The corner page's sections, each its fields in the form's order
CORNER_SECTIONS = (
    ("1. Inspection information", ("inspected_by", "inspection_date", "entity", "entity_if_other")),
    ("2. Location information", (
        "boro", "dot_corner_id", "north_south_street", "east_west_street", "traffic_control_ns", "traffic_control_ew",
        "corner_type", "corner_direction", "corner_number", "t_intersection", "number_of_ramps", "painted_roadway",
    )),
    ("3. Construction information", (
        "contractor_name", "permittee_id", "fms_id", "permit_number", "construction_completion_date", "contract_type",
        "curb_material", "sidewalk_material",
    )),
)
RAMP_TYPES = ("PERPENDICULAR", "PARALLEL", "CUT_THROUGH")


def inspection_report(corner: dict[str, Any]) -> Optional[InspectionReport]:
    """
    Gather a corner's inspection report: the corner, the ramp types its ramps are, and each live ramp with its attributes and their ADA warnings.
    Takes the corner dict (as get_corner returns it).
    Returns the InspectionReport, or None when its ramps can't be read.
    """
    rows = list_corner_ramps(corner["corner_id"])
    if rows is None:
        return None
    ramps = []
    for row in rows:
        attributes = None
        if row["attributes"] is not None:
            attributes = RampAttributesOut.model_validate(
                {**row["attributes"], "warnings": ada_warnings(row["attributes"], row["ramp_type"])}
            )
        ramps.append(InspectionRamp.model_validate({**row, "attributes": attributes}))
    types = [ramp_type for ramp_type in RAMP_TYPES if any(ramp.ramp_type == ramp_type for ramp in ramps)]
    return InspectionReport(corner=CornerOut.model_validate(corner), ramp_types=types, ramps=ramps)


def shown(field: str, value: Any) -> str:
    """
    Print a value as the form would: a choice in words, a yes or no, a date ISO, a slope with %, a length with in.
    Takes the field's name and its value.
    Returns the text ("" for a value not entered).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, (Decimal, float, int)):
        text = format(Decimal(str(value)).normalize(), "f")
        if field.endswith("_pct"):
            return f"{text}%"
        return f"{text} in" if field.endswith("_in") else text
    return CHOICE_LABELS.get(str(value), str(value))


def ramp_pages(ramps: list[InspectionRamp]) -> list[list[InspectionRamp]]:
    """
    Group a corner's ramps onto pages: one ramp a page, but a cut-through's two ramps share one, as the form has them.
    Takes the ramps, in the corner's order.
    Returns the pages, each a list of one ramp or two cut-through ramps.
    """
    pages: list[list[InspectionRamp]] = []
    cut_throughs = [ramp for ramp in ramps if ramp.ramp_type == "CUT_THROUGH"]
    for ramp in ramps:
        if ramp.ramp_type != "CUT_THROUGH":
            pages.append([ramp])
    for index in range(0, len(cut_throughs), 2):
        pages.append(cut_throughs[index:index + 2])
    return pages


def _text(c: Canvas, x: float, y: float, text: str, size: float = 9, font: str = FONT, colour: Color = black,
          width: Optional[float] = None) -> None:
    """
    Draw one line of text, cut with an ellipsis to its width.
    Takes the canvas, the baseline's start, the text, its size, font and colour, and the width it must fit (None for any).
    Returns nothing.
    """
    if width is not None and stringWidth(text, font, size) > width:
        while text and stringWidth(text + "…", font, size) > width:
            text = text[:-1]
        text += "…"
    c.setFillColor(colour)
    c.setFont(font, size)
    c.drawString(x, y, text)
    c.setFillColor(black)


def _wrapped(text: str, size: float, width: float) -> list[str]:
    """
    Break text into lines that fit a width.
    Takes the text, its size and the width.
    Returns the lines.
    """
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}".strip()
            if line and stringWidth(candidate, FONT, size) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines


def _header(c: Canvas, report: InspectionReport, title: str, page: int, pages: int) -> float:
    """
    Draw a page's heading: the report's name, the corner's intersection and borough, the page's title and its number.
    Takes the canvas, the report, the page's title, its number and the page count.
    Returns the y below the heading.
    """
    corner = report.corner
    y = PAGE_H - MARGIN
    _text(c, MARGIN, y, "PEDESTRIAN RAMP INSPECTION REPORT", 13, BOLD)
    _text(c, PAGE_W - MARGIN - 70, y, f"Page {page} of {pages}", 8, colour=GREY)
    y -= 15
    _text(c, MARGIN, y, f"{corner.north_south_street} & {corner.east_west_street}, {corner.boro}"
          f"   ·   Project {corner.project_id}", 9, colour=GREY, width=PAGE_W - 2 * MARGIN)
    y -= 6
    c.setStrokeColor(black)
    c.setLineWidth(0.8)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 18
    _text(c, MARGIN, y, title, 11, BOLD)
    return y - 16


def _section(c: Canvas, y: float, title: str) -> float:
    """
    Draw a section's title with a rule under it.
    Takes the canvas, the y to draw at and the title.
    Returns the y below it.
    """
    _text(c, MARGIN, y, title.upper(), 9, BOLD)
    c.setStrokeColor(RULE)
    c.setLineWidth(0.5)
    c.line(MARGIN, y - 3, PAGE_W - MARGIN, y - 3)
    return y - LINE - 2


def _pairs(c: Canvas, y: float, pairs: list[tuple[str, str]], columns: int = 2) -> float:
    """
    Draw label and value pairs in columns, row by row.
    Takes the canvas, the y to start at, the (label, value) pairs and how many columns.
    Returns the y below the last row.
    """
    col_w = (PAGE_W - 2 * MARGIN) / columns
    for index, (label, value) in enumerate(pairs):
        x = MARGIN + (index % columns) * col_w
        _text(c, x, y, f"{label}:", 8, colour=GREY, width=col_w * 0.48)
        _text(c, x + col_w * 0.5, y, value, 9, width=col_w * 0.48)
        if index % columns == columns - 1 or index == len(pairs) - 1:
            y -= LINE
    return y


def _paragraph(c: Canvas, y: float, label: str, text: Optional[str]) -> float:
    """
    Draw a label and a wrapped text under it (a comments field).
    Takes the canvas, the y to start at, the label and the text (None prints the label alone).
    Returns the y below it.
    """
    _text(c, MARGIN, y, f"{label}:", 8, colour=GREY)
    y -= LINE
    for line in _wrapped(text or "", 9, PAGE_W - 2 * MARGIN - 10)[:12]:
        _text(c, MARGIN + 10, y, line, 9)
        y -= LINE - 1
    return y


def _corner_page(c: Canvas, report: InspectionReport, pages: int) -> None:
    """
    Draw page 1: the corner's sections 1 to 3, its ramp types (section 4) and its ramps.
    Takes the canvas, the report and the page count.
    Returns nothing.
    """
    corner = report.corner.model_dump()
    y = _header(c, report, "Corner", 1, pages)
    for title, fields in CORNER_SECTIONS:
        y = _section(c, y, title)
        y = _pairs(c, y, [(CORNER_LABELS[field], shown(field, corner.get(field))) for field in fields]) - 6
    y = _section(c, y, "4. Corner ramp type")
    x = MARGIN
    for ramp_type in RAMP_TYPES:
        c.setStrokeColor(black)
        c.rect(x, y - 1, 8, 8)
        if ramp_type in report.ramp_types:
            _text(c, x + 1.5, y, "X", 8, BOLD)
        _text(c, x + 13, y, CHOICE_LABELS[ramp_type] + " ramp", 9)
        x += 150
    y -= LINE + 8
    y = _section(c, y, f"Ramps at this corner ({len(report.ramps)})")
    if not report.ramps:
        _text(c, MARGIN, y, "No ramps are placed at this corner yet.", 9, colour=GREY)
        y -= LINE
    for ramp in report.ramps:
        warnings = len(ramp.attributes.warnings) if ramp.attributes else 0
        status = ("attributes not entered" if ramp.attributes is None
                  else f"{warnings} ADA warning{'s' if warnings != 1 else ''}" if warnings else "within the advisories")
        label = f" ({ramp.ramp_id_label})" if ramp.ramp_id_label else ""
        _text(c, MARGIN, y, f"{CHOICE_LABELS[ramp.ramp_type]}{label}", 9, BOLD, width=150)
        _text(c, MARGIN + 155, y, ramp.display_address, 9, width=260)
        _text(c, MARGIN + 420, y, status, 8, colour=RED if warnings else GREY, width=PAGE_W - 2 * MARGIN - 420)
        y -= LINE
    y -= 6
    _paragraph(c, y, CORNER_LABELS["comments"], report.corner.comments)


def _field_rows(c: Canvas, x: float, y: float, width: float, ramp: InspectionRamp, fields: tuple[str, ...]) -> float:
    """
    Draw a ramp's fields as label and value rows in one column, a value with a warning in red and marked.
    Takes the canvas, the column's left x, the y to start at, its width, the ramp and its fields in order.
    Returns the y below the last row.
    """
    attrs = ramp.attributes.model_dump() if ramp.attributes else {}
    warned = {warning.field for warning in ramp.attributes.warnings} if ramp.attributes else set()
    for field in fields:
        bad = field in warned
        _text(c, x, y, FIELD_LABELS[field], 8, colour=GREY, width=width * 0.6)
        value = shown(field, attrs.get(field))
        _text(c, x + width * 0.62, y, value + ("  (ADA)" if bad and value else ""), 9, BOLD if bad else FONT,
              RED if bad else black, width=width * 0.38)
        y -= LINE
    return y


def _warnings(c: Canvas, y: float, ramps: list[InspectionRamp]) -> None:
    """
    Draw the warnings of a page's ramps at its foot (a cut-through's named by ramp).
    Takes the canvas, the y to start at and the page's ramps.
    Returns nothing.
    """
    y = _section(c, y - 4, "ADA advisories")
    listed: list[tuple[str, AdaWarning]] = []
    for index, ramp in enumerate(ramps):
        prefix = f"Ramp {index + 1}: " if len(ramps) > 1 else ""
        for warning in ramp.attributes.warnings if ramp.attributes else []:
            listed.append((prefix, warning))
    missing = [index for index, ramp in enumerate(ramps) if ramp.attributes is None]
    if len(ramps) > 1:
        for index in missing:
            _text(c, MARGIN, y, f"Ramp {index + 1}: attributes not entered.", 9, colour=GREY)
            y -= LINE - 1
    if not listed:
        if len(missing) < len(ramps) or len(ramps) == 1:
            _text(c, MARGIN, y, "Attributes not entered." if missing else "Every value entered is within the advisories.",
                  9, colour=GREY)
        return
    for prefix, warning in listed:
        if y < MARGIN:
            _text(c, MARGIN, y + LINE - 4, "… more warnings than fit; see ICID.", 8, colour=GREY)
            return
        _text(c, MARGIN, y, f"• {prefix}{warning.message}", 8.5, colour=RED,
              width=PAGE_W - 2 * MARGIN)
        y -= LINE - 1


def _ramp_page(c: Canvas, report: InspectionReport, ramps: list[InspectionRamp], page: int, pages: int) -> None:
    """
    Draw a ramp's section 5 as the form lays it out (its answers left and its measurements right; a cut-through's two ramps side by side), its comments and its warnings.
    Takes the canvas, the report, the page's ramps, its number and the page count.
    Returns nothing.
    """
    ramp_type = ramps[0].ramp_type
    title = f"5. Ramp attributes: {CHOICE_LABELS[ramp_type]}" + (" ramp" if ramp_type != "CUT_THROUGH" else "")
    y = _header(c, report, title, page, pages)
    fields = tuple(field for field in COMMON_FIELDS if field != "comments") + TYPE_FIELDS[ramp_type]
    usable = PAGE_W - 2 * MARGIN
    if len(ramps) == 1:
        ramp = ramps[0]
        _text(c, MARGIN, y, ramp.display_address, 9, colour=GREY, width=usable)
        y -= LINE + 4
        # As the form: the answers on the left, the measurements of the ramp type on the right
        measured = tuple(field for field in TYPE_FIELDS[ramp_type] if field.endswith(("_pct", "_in")))
        width = usable / 2 - 10
        left = _field_rows(c, MARGIN, y, width, ramp, tuple(field for field in fields if field not in measured))
        right = _field_rows(c, MARGIN + usable / 2 + 10, y, width, ramp, measured)
        y = min(left, right) - 4
    else:
        width = usable / len(ramps) - 10
        bottoms = []
        for index, ramp in enumerate(ramps):
            x = MARGIN + index * (usable / len(ramps)) + (10 if index else 0)
            _text(c, x, y, f"RAMP {index + 1}", 9, BOLD)
            _text(c, x + 45, y, ramp.display_address, 8, colour=GREY, width=width - 45)
            bottoms.append(_field_rows(c, x, y - LINE - 4, width, ramp, fields))
        y = min(bottoms) - 4
    for index, ramp in enumerate(ramps):
        label = FIELD_LABELS["comments"] + (f" (ramp {index + 1})" if len(ramps) > 1 else "")
        y = _paragraph(c, y, label, ramp.attributes.comments if ramp.attributes else None) - 2
    _warnings(c, y - 4, ramps)


def render_pdf(report: InspectionReport) -> bytes:
    """
    Draw a corner's inspection report as a PDF: the corner page, then a page per ramp (a cut-through's two on one).
    Takes the InspectionReport.
    Returns the PDF's bytes.
    """
    buffer = io.BytesIO()
    c = Canvas(buffer, pagesize=letter, pageCompression=1)
    c.setTitle(f"Pedestrian ramp inspection: {report.corner.north_south_street} & {report.corner.east_west_street}")
    pages = ramp_pages(report.ramps)
    _corner_page(c, report, len(pages) + 1)
    for index, ramps in enumerate(pages):
        c.showPage()
        _ramp_page(c, report, ramps, index + 2, len(pages) + 1)
    c.showPage()
    c.save()
    return buffer.getvalue()


def file_name(corner: CornerOut) -> str:
    """
    Name a corner's report file after its intersection and corner.
    Takes the corner.
    Returns the file name, as "Main_St_and_1st_Ave_SW_ped_ramp_inspection.pdf".
    """
    parts = [corner.north_south_street, "and", corner.east_west_street, corner.corner_direction or ""]
    stem = "_".join("".join(ch if ch.isalnum() else "_" for ch in part).strip("_") for part in parts if part)
    return f"{stem}_ped_ramp_inspection.pdf"
