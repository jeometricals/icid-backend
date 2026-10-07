"""
Stamps an IDR's Concrete Truck & Mix Info (CONC_MIX) addendum onto the DDC template's Conc Mix page.

Conc Mix is a single page: a compact header (project details, date, page number and inspector; no day of the week,
times, temperatures or weather), Location of Use, Mixer Type, the Trucks table, Concrete Specifications, Material
Usage and Remarks. The Trucks table holds 11 trucks; a report with more continues on clones of the page (Conc Mix 2,
Conc Mix 3, ...), each a full copy of the form with the next 11 trucks.

Cell positions come from reading templates/report_forms.xlsx.
"""

from typing import Any, Optional

from api.services.export_common import (
    SignatureLayout,
    CHECK_MARK, PAY_REDLINE_COLOR, REDLINE_INITIALS_FONT_PT, TextArea, allocate_copies, checklist_answer,
    entry_edits, fill_lines, ir_number, mark_truncated, redline_paragraphs, redline_runs,
    rows_with_paths, section, short_date, stamp_answer_box, stamp_field, text_value, write_lines,
)
from api.services.export_redlines import NO_REDLINES, RedlineEntry, Redlines, redline_chain
from api.services.xlsx_template import EMU_PER_PIXEL, TextRun, WorkbookTemplate

CONC_MIX = "Conc Mix"

# Conc Mix's signature line is row 60 (C60:P60), its date AK60:AO60; the image also takes the blank row above.
# Its columns are 16 px, and row 59 is 15 px (11.25 pt), so the box is 14 x 16 = 224 px by 15 + 17 = 32 px.
SIGNATURE_LAYOUT = SignatureLayout(signature_cells="C59:P60", signature_cx_emu=224 * EMU_PER_PIXEL,
                                   signature_cy_emu=32 * EMU_PER_PIXEL, date_cell="AK60", column_px=16,
                                   row_px=(15, 17))
# The Resident Engineer's line is W60:AI60, its caption W61:AI61. Column X is 11 px, the other twelve 16 px, so the
# box is 203 px wide.
RE_SIGNATURE_LAYOUT = SignatureLayout(signature_cells="W59:AI60", signature_cx_emu=203 * EMU_PER_PIXEL,
                                      signature_cy_emu=32 * EMU_PER_PIXEL, row_px=(15, 17),
                                      column_widths_px=(16, 11) + (16,) * 11, caption_cells="W61:AI61")

# The header. HeaderLayout doesn't fit (it requires the day, times, temperatures and weather this form lacks), so it
# is stamped here, as Report Cont's is. The template has formulas reading Contract Info in the project cells, replaced
# by the values themselves; F14 is the top left of the two-row F14:V15 merge. The date, page and "of" cells (AD8,
# AD10, AJ10) are unmerged, General-formatted blanks on an underline, so the date goes in as m/d/yy text. The
# inspector's line H17:V17 is three merges (H17:M17, N17:Q17, R17:V17); the name goes in the first.
CONC_MIX_PROJECT_CELLS = {"G8": "project_id", "P8": "registration_code", "I10": "project_description",
                          "F12": "borough", "F14": "contractor"}
CONC_MIX_DATE = "AD8"
CONC_MIX_SHEET_NO = "AD10"
CONC_MIX_SHEET_OF = "AJ10"
CONC_MIX_INSPECTOR = "H17"
CONC_MIX_IR_NO = "AJ17"  # "ATTACHMENT TO I.R. NO.": left blank, the I.R. number is assigned later

# The checkboxes (Location of Use on row 22, Mixer Type on row 25) are 10 x 10 px squares drawn as four lines, with no
# fill. Columns are 16 px (X is 11 px) and rows 17 px. All but Other's box straddle two cells, centred on the line
# between them, so the "X" is centred across both (Center Across Selection, no merge); Other's sits inside T25.
# 7 pt keeps the X inside the box, scaled from Conc Fr's 6 pt in an 8 px box.
LOCATION_BOXES = {"curb": ("F22", "G22"), "sidewalk": ("N22", "O22"), "concreteBase": ("X22", "Y22"),
                  "structural": ("AF22", "AG22")}
MIXER_BOXES = {"readyMix": ("O25", "P25"), "other": ("T25",)}
CHECK_MARK_FONT_PT = 7
MIXER_OTHER_LABEL = "X25"  # X25:AO25, after the "Other" box and label
# Where the initials of a reviewer who changed a box go: the blank cell right after it (each box is followed by two
# or more before the next label). Other's box is followed by its label, so its initials open the write-in cell.
LOCATION_INITIALS = {"curb": "H22", "sidewalk": "P22", "concreteBase": "Z22", "structural": "AH22"}
MIXER_INITIALS = {"readyMix": "Q25", "other": MIXER_OTHER_LABEL}

# The Trucks table: rows 28-38, one merged area per column. The Inspection Sticker's "Y" (G:H) and "N" (I:J) are
# pre-printed in every row; the answer's letter is replaced by an "X". N/A has no box, so it leaves both letters.
TRUCK_ROWS = range(28, 39)
TRUCK_COLUMNS = {"truckOrTicketNo": "B", "loadSizeCy": "K", "endBatch": "N", "mixingRevs": "Q",
                 "startDischTime": "T", "endDischTime": "W", "slump": "Z", "airContent": "AC", "concTemp": "AH",
                 "cylinderNumbers": "AK"}
STICKER_CELLS = {"Y": "G", "N": "I"}

# Concrete Specifications. "Class of Concrete: ________________" is one text cell (B41:O41), so it's rewritten with
# the value in place of the blank. Min / Max for Slump (row 43) and Air (row 44) are cells holding a single space.
# Row 45 is a third, unlabelled test row the form leaves for writing in; the frontend has no field for it.
CLASS_OF_CONCRETE = "B41"
CLASS_OF_CONCRETE_LABEL = "Class of Concrete:"
CLASS_OF_CONCRETE_BLANK = "________________"
SPEC_CELLS = {"slumpMin": "H43", "slumpMax": "L43", "airMin": "H44", "airMax": "L44"}

# Material Usage: values go right of each "=" (W41:Z41 ... for the left column, AL41:AP41 ... for the right).
MATERIAL_CELLS = {"batchReportNo": "W41", "noOfTickets": "W42", "firstTicketNo": "W43", "lastTicketNo": "W44",
                  "quantityDispatched": "AL41", "quantityReceived": "AL42", "quantityUsed": "AL43",
                  "quantityWasted": "AL44"}

# Remarks: six ruled 8 pt lines. The first shares row 49 with the "REMARKS:" label (C49:G49), so it starts at H49 and
# runs to AO (539 px); the other five run C50:AO54 (619 px). Conc Fr's 10 pt lines hold 84 characters across 651 px,
# so at 8 pt a pixel holds 84 x 10 / 8 / 651 = 0.16 characters: 86 on the first line, 99 on the rest.
# The inspector's remarks print on the first sheet only. A sheet followed by another ends its remarks with
# CONTINUED_NEXT; each later sheet's remarks hold just CONTINUED_FROM (no sheet name or page number: tab names aren't
# printed, and a draft's pages aren't numbered).
REMARKS_FIRST = TextArea(rows=range(49, 50), column="H", line_chars=86)
REMARKS_REST = TextArea(rows=range(50, 55), column="C", line_chars=99)
CONTINUED_NEXT = "Continued on next page"
CONTINUED_FROM = "(Continued from previous page)"


def truck_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Read the Trucks table from report_data, tolerating a missing list or malformed rows.
    Takes the report_data.
    Returns the trucks (each a dict), in the inspector's order, each with "_path": the field_path an edit names it
    by ("trucks[0]", its place in the saved list, whichever sheet it prints on).
    """
    return rows_with_paths(data, "trucks")


def sheet_count(report_data: Any) -> int:
    """
    Count the Conc Mix sheets a report prints on: one per 11 trucks, and always at least one.
    Takes the report_data (anything that isn't an object counts as empty).
    Returns the number of sheets.
    """
    trucks = truck_rows(report_data if isinstance(report_data, dict) else {})
    return max(1, -(-len(trucks) // len(TRUCK_ROWS)))


def allocate_sheets(workbook: WorkbookTemplate, report_data: Any, first_index: int = 0) -> list[str]:
    """
    Provide the sheets one CONC_MIX report prints on (Conc Mix, Conc Mix 2, ... numbered across the IDR's CONC_MIX
    reports), cloning the blank Conc Mix for each past the template's own and placing each after the one before.
    Call it before anything is stamped on Conc Mix, so clones start blank.
    Takes the workbook, the report_data (its trucks decide the count) and the position of the report's first sheet
    among the IDR's Conc Mix sheets (0 for the first report).
    Returns the report's sheet names, in order.
    """
    sheets = allocate_copies(workbook, CONC_MIX, sheet_count(report_data), first_index)
    for previous, sheet in zip(sheets, sheets[1:]):
        workbook.move_sheet(sheet, after=previous)
    return sheets


def _stamp_header(workbook: WorkbookTemplate, sheet: str, idr: dict[str, Any], project: dict[str, Any],
                  contractor: Optional[str], inspector: Optional[str], page_number: Optional[int]) -> None:
    """
    Write a Conc Mix sheet's header: project details (as values, not Contract Info formulas), date, page number and
    inspector. Takes the workbook, the sheet, the IDR row (its total_pages fills OF), the project row, the contractor's
    and inspector's names, and the sheet's page number (None leaves PAGE / OF blank).
    Returns nothing; ATTACHMENT TO I.R. NO. takes the IDR's number, and stays blank for an IDR without one.
    """
    for cell, field in CONC_MIX_PROJECT_CELLS.items():
        workbook.set_cell(sheet, cell, contractor if field == "contractor" else project.get(field))
    workbook.set_cell(sheet, CONC_MIX_DATE, short_date(idr["report_date"]))
    has_page = page_number is not None
    workbook.set_cell(sheet, CONC_MIX_SHEET_NO, page_number if has_page else None)
    workbook.set_cell(sheet, CONC_MIX_SHEET_OF, idr.get("total_pages") if has_page else None)
    workbook.set_cell(sheet, CONC_MIX_INSPECTOR, inspector)
    workbook.set_cell(sheet, CONC_MIX_IR_NO, ir_number(idr))


def _tick(workbook: WorkbookTemplate, sheet: str, box: tuple[str, ...], ticked: bool) -> None:
    """
    Tick (or clear) one drawn checkbox: a 7 pt "X" centred across the cells under it.
    Takes the workbook, the sheet, the cells the box covers (left to right) and whether to tick it.
    Returns nothing.
    """
    workbook.set_cell(sheet, box[0], CHECK_MARK if ticked else None)
    if ticked:
        workbook.set_font_size(sheet, box[0], CHECK_MARK_FONT_PT)
        workbook.center_across(sheet, list(box))


def _stamp_choice(workbook: WorkbookTemplate, sheet: str, boxes: dict[str, tuple[str, ...]],
                  chain: list[RedlineEntry]) -> Optional[str]:
    """
    Mark the boxes of a choice a reviewer edited: an X in the box of the value that stands (in the redline colour
    when it is a reviewer's), a struck X in each box a value left (black for the inspector's), the others empty.
    Takes the workbook, the sheet, each value's box (the cells it covers) and the choice's chain.
    Returns the value whose box the editors' initials go beside: the one that stands, else the last one struck;
    None when the inspector changed the choice after the last edit (the boxes then show it plainly) or no box is
    marked.
    """
    standing = chain[-1]
    beside = None
    for value, box in boxes.items():
        replaced = [entry for entry in chain[:-1] if entry.text == value]
        if standing.revised or (standing.text != value and not replaced):
            _tick(workbook, sheet, box, standing.text == value)
            continue
        entry = standing if standing.text == value else replaced[-1]
        mark = TextRun(CHECK_MARK, color=PAY_REDLINE_COLOR if entry.by_reviewer else None, points=CHECK_MARK_FONT_PT,
                       strike=standing.text != value)
        workbook.set_cell_runs(sheet, box[0], [mark])
        workbook.center_across(sheet, list(box))
        if standing.text == value or beside is None:
            beside = value
    return None if standing.revised else beside


def _chain_initials(chain: list[RedlineEntry]) -> Optional[str]:
    """
    Collect the initials of everyone who edited a field.
    Takes the field's chain.
    Returns them in order, each once, space-separated; None when there are none.
    """
    return " ".join(dict.fromkeys(entry.initials for entry in chain if entry.initials)) or None


def _stamp_box_initials(workbook: WorkbookTemplate, sheet: str, cell: str, initials: Optional[str]) -> None:
    """
    Write the initials of whoever changed a box in the cell right after it, small and in the redline colour.
    Takes the workbook, the sheet, the cell and the initials (None writes nothing).
    Returns nothing.
    """
    if initials:
        workbook.set_cell_runs(sheet, cell, [TextRun(initials, color=PAY_REDLINE_COLOR,
                                                     points=REDLINE_INITIALS_FONT_PT)])
        workbook.align_left(sheet, cell)


def _stamp_location_and_mixer(workbook: WorkbookTemplate, sheet: str, data: dict[str, Any],
                              redlines: Redlines = NO_REDLINES) -> None:
    """
    Tick the Location of Use and Mixer Type boxes, and write the mixer type when it's Other. A box a reviewer
    changed is marked (see _stamp_choice) with their initials right after it; Other's go at the start of its
    write-in cell, whose text prints as its chain when a reviewer edited it.
    Takes the workbook, the sheet, the report_data and the report's redlines (none unless given).
    Returns nothing.
    """
    location = section(data, "locationOfUse")
    for key, box in LOCATION_BOXES.items():
        chain = redline_chain(redlines.field(f"locationOfUse.{key}"), location.get(key),
                              lambda value: CHECK_MARK if value is True else None)
        if not chain:
            _tick(workbook, sheet, box, location.get(key) is True)
        elif _stamp_choice(workbook, sheet, {CHECK_MARK: box}, chain):
            _stamp_box_initials(workbook, sheet, LOCATION_INITIALS[key], _chain_initials(chain))

    mixer = section(data, "mixerType")
    chain = redline_chain(redlines.field("mixerType.type"), mixer.get("type"),
                          lambda value: value if value in MIXER_BOXES else None)
    beside = _stamp_choice(workbook, sheet, MIXER_BOXES, chain) if chain else None
    if not chain:
        for kind, box in MIXER_BOXES.items():
            _tick(workbook, sheet, box, mixer.get("type") == kind)
    initials = _chain_initials(chain) if beside else None
    if beside is not None and beside != "other":
        _stamp_box_initials(workbook, sheet, MIXER_INITIALS[beside], initials)

    is_other = mixer.get("type") == "other"
    label_edits = redlines.field("mixerType.otherLabel") if is_other else []
    opening = initials if beside == "other" else None
    if not label_edits and not opening:
        workbook.set_cell(sheet, MIXER_OTHER_LABEL, text_value(mixer.get("otherLabel")) if is_other else None)
        return
    label = text_value(mixer.get("otherLabel")) if is_other else None
    runs = [TextRun(f"{opening} ", color=PAY_REDLINE_COLOR, points=REDLINE_INITIALS_FONT_PT)] if opening else []
    runs += redline_runs(redline_chain(label_edits, mixer.get("otherLabel"))) or ([TextRun(label)] if label else [])
    workbook.set_cell_runs(sheet, MIXER_OTHER_LABEL, runs)
    workbook.shrink_on_one_line(sheet, MIXER_OTHER_LABEL)


def _stamp_trucks(workbook: WorkbookTemplate, sheet: str, trucks: list[dict[str, Any]],
                  redlines: Redlines = NO_REDLINES) -> None:
    """
    Fill the Trucks table, one truck per row, and mark each one's inspection sticker. A value a reviewer edited
    prints as its chain; a sticker answer they changed keeps a struck X where it was and gets an X in the redline
    colour, followed by their initials. A truck a reviewer added has every cell it fills in the redline colour, its
    sticker's X included, and the adder's initials after its truck or ticket number.
    Takes the workbook, the sheet, its trucks (at most 11, each with the path edits name it by, see truck_rows; rows
    past them stay blank, their Y / N letters kept) and the report's redlines (none unless given).
    Returns nothing.
    """
    for index, row in enumerate(TRUCK_ROWS):
        truck = trucks[index] if index < len(trucks) else {}
        added_by = redlines.added_truck(truck)
        for field, column in TRUCK_COLUMNS.items():
            cell, edits = f"{column}{row}", entry_edits(redlines, truck, field)
            stamp_field(workbook, sheet, cell, truck.get(field), edits)
            if added_by is not None and (edits or text_value(truck.get(field))):
                workbook.set_font_color(sheet, cell, PAY_REDLINE_COLOR)
        number = text_value(truck.get("truckOrTicketNo"))
        if added_by and not entry_edits(redlines, truck, "truckOrTicketNo"):
            cell = f"{TRUCK_COLUMNS['truckOrTicketNo']}{row}"
            workbook.set_cell_runs(sheet, cell, [TextRun(number or "", color=PAY_REDLINE_COLOR),
                                                 TextRun(f" {added_by}" if number else added_by,
                                                         color=PAY_REDLINE_COLOR, points=REDLINE_INITIALS_FONT_PT)])
            workbook.shrink_on_one_line(sheet, cell)
        sticker = truck.get("inspectionSticker")
        chain = redline_chain(entry_edits(redlines, truck, "inspectionSticker"), sticker, checklist_answer)
        for answer, column in STICKER_CELLS.items():
            cell = f"{column}{row}"
            if chain:
                stamp_answer_box(workbook, sheet, cell, answer, chain, _chain_initials(chain), blank=answer)
            elif sticker == answer and added_by is not None:
                workbook.set_cell_runs(sheet, cell, [TextRun(CHECK_MARK, color=PAY_REDLINE_COLOR)])
            else:
                workbook.set_cell(sheet, cell, CHECK_MARK if sticker == answer else answer)


def _stamp_specs_and_materials(workbook: WorkbookTemplate, sheet: str, data: dict[str, Any],
                               redlines: Redlines = NO_REDLINES) -> None:
    """
    Write Concrete Specifications (class of concrete, slump and air ranges) and Material Usage. A value a reviewer
    edited prints as its chain; the class of concrete's follows its label.
    Takes the workbook, the sheet, the report_data and the report's redlines (none unless given).
    Returns nothing.
    """
    specs = section(data, "concreteSpecs")
    class_chain = redline_chain(redlines.field("concreteSpecs.classOfConcrete"), specs.get("classOfConcrete"))
    if class_chain:
        workbook.set_cell_runs(sheet, CLASS_OF_CONCRETE,
                               [TextRun(f"{CLASS_OF_CONCRETE_LABEL} ")] + redline_runs(class_chain))
        workbook.shrink_on_one_line(sheet, CLASS_OF_CONCRETE)
    else:
        concrete_class = text_value(specs.get("classOfConcrete")) or CLASS_OF_CONCRETE_BLANK
        workbook.set_cell(sheet, CLASS_OF_CONCRETE, f"{CLASS_OF_CONCRETE_LABEL} {concrete_class}")
    for field, cell in SPEC_CELLS.items():
        stamp_field(workbook, sheet, cell, specs.get(field), redlines.field(f"concreteSpecs.{field}"))
    materials = section(data, "materialUsage")
    for field, cell in MATERIAL_CELLS.items():
        stamp_field(workbook, sheet, cell, materials.get(field), redlines.field(f"materialUsage.{field}"))


def remarks_lines(text: list[str], continued: bool) -> tuple[list[str], list[str]]:
    """
    Lay a sheet's remarks out on its six lines, keeping the last one free for CONTINUED_NEXT when a sheet follows.
    Takes the remarks' paragraphs and whether another Conc Mix sheet follows.
    Returns (the first line's text, the other lines' text); text that doesn't fit is cut with "continued in ICID".
    """
    queue = list(text)
    first = fill_lines(queue, len(REMARKS_FIRST.rows), REMARKS_FIRST.line_chars)
    rest = fill_lines(queue, len(REMARKS_REST.rows) - (1 if continued else 0), REMARKS_REST.line_chars)
    if queue:
        rest = mark_truncated(rest, REMARKS_REST.line_chars)
    if continued:
        (rest if first else first).append(CONTINUED_NEXT)
    return first, rest


def _stamp_remarks(workbook: WorkbookTemplate, sheet: str, text: list[str], continued: bool) -> None:
    """
    Write a sheet's remarks on its six lines.
    Takes the workbook, the sheet, the remarks' paragraphs and whether another Conc Mix sheet follows.
    Returns nothing.
    """
    first, rest = remarks_lines(text, continued)
    write_lines(workbook, sheet, REMARKS_FIRST.rows, first, REMARKS_FIRST.column)
    write_lines(workbook, sheet, REMARKS_REST.rows, rest, REMARKS_REST.column)


def render(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
           inspector: Optional[str] = None, page_number: Optional[int] = None,
           report_data: Optional[dict[str, Any]] = None, sheets: Optional[list[str]] = None,
           redlines: Redlines = NO_REDLINES) -> list[str]:
    """
    Stamp a CONC_MIX report onto its Conc Mix sheets: the full form on every one, the trucks split 11 a sheet.
    What reviewers edited (the location and mixer boxes, the trucks, the specifications, material usage and the
    remarks) prints with its redlines; pass them as redlines (none unless given).
    Takes the workbook, the IDR row (its total_pages fills OF), the project row, the contractor's and inspector's names,
    the report's page number (its later sheets take the numbers after it; None leaves PAGE / OF blank), its
    report_data (None stamps the header and an empty body) and the sheets to use, from allocate_sheets (None
    allocates them here, starting at Conc Mix, for an IDR's only CONC_MIX report).
    Returns the sheets it used, in order, each set to print on one Letter page; the caller decides which show.
    Raises ValueError if the sheets given don't match the count the trucks need.
    """
    data = report_data if isinstance(report_data, dict) else {}
    trucks = truck_rows(data)
    sheets = sheets if sheets is not None else allocate_sheets(workbook, data)
    if len(sheets) != sheet_count(data):
        raise ValueError(f"{len(trucks)} trucks need {sheet_count(data)} Conc Mix sheets, got {len(sheets)}")

    for index, sheet in enumerate(sheets):
        page = page_number + index if page_number is not None else None
        _stamp_header(workbook, sheet, idr, project, contractor, inspector, page)
        _stamp_location_and_mixer(workbook, sheet, data, redlines)
        _stamp_trucks(workbook, sheet, trucks[index * len(TRUCK_ROWS):(index + 1) * len(TRUCK_ROWS)], redlines)
        _stamp_specs_and_materials(workbook, sheet, data, redlines)
        remarks = redline_paragraphs(data.get("remarks"), redlines.field("remarks"))
        text = remarks if index == 0 else [CONTINUED_FROM]
        _stamp_remarks(workbook, sheet, text, continued=index < len(sheets) - 1)
        workbook.fit_to_letter_page(sheet)
    return sheets
