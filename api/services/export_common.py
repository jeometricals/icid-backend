"""
What the DDC report forms share: the IDR header block (project details, date and day, I.R. No., sheet number, times,
temperatures, weather and inspector), the Pay Items table, laying free text out on ruled lines, and the value
formatting used to fill them.

Each form puts these in its own cells; a HeaderLayout or PayItemsLayout names them for one sheet, and the stamp_*
functions fill them. Per-report modules (export_general, export_swcb, ...) own their sheets' layouts.
"""

import textwrap
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from api.services.xlsx_template import WorkbookTemplate

CHECK_MARK = "X"
TEXT_OVERFLOW = " … (continued in ICID)"

# A pay item's description gets at most two lines: at the template's 10 pt, then shrunk to 8 pt, then cut with "...".
# Its rows are fixed-height (one line), and Excel won't grow rows holding merged cells, so two lines get a taller row.
PAY_DESCRIPTION_FONT_PT = 10
PAY_DESCRIPTION_SHRINK_FONT_PT = 8
PAY_DESCRIPTION_MAX_LINES = 2
PAY_DESCRIPTION_TRUNCATE_SUFFIX = "..."
LINE_HEIGHT = {10: 12.75, 8: 11.25}  # points per line of Arial at that size (Excel's default row heights)


@dataclass(frozen=True)
class PayItemsLayout:
    """Where one form's Pay Items table is, and how many description characters fit a line at 10 pt and 8 pt."""

    rows: range
    columns: dict[str, str]  # itemNo, budgetCode, payQuantity, quantityChk, description -> column letter
    line_chars_10pt: int
    line_chars_8pt: int


@dataclass(frozen=True)
class HeaderLayout:
    """Where one form's header fields go. Cells are each merged area's top-left cell."""

    project_cells: dict[str, str]   # cell -> project field ("contractor" for the contractor's name); the template has
                                    # formulas reading Contract Info there, replaced by the values themselves
    date: str
    date_as_text: bool              # True where the date cell is General-formatted (the date is written as m/d/yy text)
    day_of_week: tuple[str, ...]    # the S M T W T F S letter cells, Sunday first
    ir_no: str
    sheet_no: str
    sheet_of: str
    work_time: str                  # "( Start ___ End ___ )"
    inspector_time: str
    temp_low: str                   # the "Low" / "High" label shares the cell with the value
    temp_high: str
    weather_am: str
    weather_pm: str
    weather_labels: Optional[tuple[str, str]]  # ("AM", "PM") where the label shares the weather box with the value
    inspector: str


def text_value(value: Any) -> Optional[str]:
    """
    Normalise a free-text field for a cell.
    Takes the value.
    Returns the trimmed string, or None when blank or missing.
    """
    text = str(value).strip() if value is not None else ""
    return text or None


def section(data: dict[str, Any], key: str) -> dict[str, Any]:
    """
    Read one object-valued section of report_data, tolerating a missing or malformed one.
    Takes the report_data and the section's key.
    Returns the section, or {} when it isn't an object.
    """
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def short_date(day: date) -> str:
    """
    Format a date the way the forms' date-formatted cells show it (m/d/yy), for cells that hold it as text.
    Takes the date.
    Returns e.g. "9/30/26".
    """
    return f"{day.month}/{day.day}/{day:%y}"


def time_range(start: Optional[time], end: Optional[time]) -> Optional[str]:
    """
    Fill the template's "( Start ___ End ___ )" line.
    Takes the start and end times (either may be None).
    Returns the line with HH:MM for each known time, or None (a blank cell) when neither is known.
    """
    if start is None and end is None:
        return None

    def slot(value: Optional[time]) -> str:
        return value.strftime("%H:%M") if value else "________"
    return f"( Start {slot(start)} End {slot(end)} )"


def temperature(label: str, value: Optional[Decimal]) -> str:
    """
    Fill a Low / High temperature box, whose label shares the cell with the value.
    Takes the label and the temperature (or None).
    Returns e.g. "Low  45" (whole numbers without decimals), or just the label when unknown.
    """
    if value is None:
        return label
    number = Decimal(value)
    shown = str(number.quantize(Decimal(1))) if number == number.to_integral_value() else str(number.normalize())
    return f"{label}  {shown}"


def highlight_day(workbook: WorkbookTemplate, sheet: str, cells: tuple[str, ...], day: date) -> None:
    """
    Mark the report's day of the week among a form's S M T W T F S letters (the form circles it by hand).
    Takes the workbook, sheet, the seven letter cells (Sunday first) and the date.
    Returns nothing.
    """
    cell = cells[(day.weekday() + 1) % 7]
    workbook.set_style(sheet, cell, workbook.highlighted_style(workbook.cell_style(sheet, cell)))


def _weather(workbook: WorkbookTemplate, sheet: str, cell: str, label: Optional[str], value: Any) -> None:
    """
    Write one weather box: the value alone, or under the box's own label when the two share the cell.
    Takes the workbook, sheet, cell, the shared label (None when the box holds only the value) and the weather.
    Returns nothing; a shared box gets "AM" and the value on separate lines, so it wraps.
    """
    weather = text_value(value)
    if label is None:
        workbook.set_cell(sheet, cell, weather)
        return
    workbook.set_cell(sheet, cell, f"{label}\n{weather}" if weather else label)
    if weather:
        workbook.wrap_cell(sheet, cell)


def stamp_common_header(workbook: WorkbookTemplate, sheet: str, layout: HeaderLayout, idr: dict[str, Any],
                        project: dict[str, Any], contractor: Optional[str], inspector: Optional[str],
                        page_number: Optional[int]) -> None:
    """
    Write a form's header block: project details (as values, not Contract Info formulas), date, day, times,
    temperatures, weather, sheet number and inspector, clearing the template's placeholders when a value is missing.
    Takes the workbook, sheet name, its layout, the IDR row, the project row, the contractor's and inspector's names,
    and this page's number (None leaves Sheet No. blank, e.g. for a page that isn't one of the IDR's numbered pages).
    Returns nothing; I.R. No. stays blank, as the data model has no source for it yet.
    """
    for cell, field in layout.project_cells.items():
        workbook.set_cell(sheet, cell, contractor if field == "contractor" else project.get(field))
    report_date: date = idr["report_date"]
    workbook.set_cell(sheet, layout.date, short_date(report_date) if layout.date_as_text else report_date)
    highlight_day(workbook, sheet, layout.day_of_week, report_date)
    workbook.set_cell(sheet, layout.ir_no, None)
    has_page = page_number is not None
    workbook.set_cell(sheet, layout.sheet_no, page_number if has_page else None)
    workbook.set_cell(sheet, layout.sheet_of, idr.get("total_pages") if has_page else None)
    workbook.set_cell(sheet, layout.work_time, time_range(idr.get("work_start_time"), idr.get("work_end_time")))
    workbook.set_cell(sheet, layout.inspector_time,
                      time_range(idr.get("inspector_start_time"), idr.get("inspector_end_time")))
    workbook.set_cell(sheet, layout.temp_low, temperature("Low", idr.get("temp_low")))
    workbook.set_cell(sheet, layout.temp_high, temperature("High", idr.get("temp_high")))
    am_label, pm_label = layout.weather_labels or (None, None)
    _weather(workbook, sheet, layout.weather_am, am_label, idr.get("weather_am"))
    _weather(workbook, sheet, layout.weather_pm, pm_label, idr.get("weather_pm"))
    workbook.set_cell(sheet, layout.inspector, inspector)


# ---- Text layout ------------------------------------------------------------

def paragraphs(text: Any) -> list[str]:
    """
    Split free text into the paragraphs that each start a new line on the form.
    Takes the text (anything that isn't a string counts as empty).
    Returns its non-blank lines, trimmed.
    """
    return [line.strip() for line in text.splitlines() if line.strip()] if isinstance(text, str) else []


def fill_lines(queue: list[str], capacity: int, width: int) -> list[str]:
    """
    Take as much text as fits in a block of ruled lines, leaving the rest queued.
    Takes the queued paragraphs (consumed in place), the number of lines and the characters per line.
    Returns the lines, each paragraph starting a new one; a paragraph cut short stays queued with its remainder.
    """
    lines: list[str] = []
    while queue and len(lines) < capacity:
        wrapped = textwrap.wrap(queue[0], width)
        room = capacity - len(lines)
        lines.extend(wrapped[:room])
        if len(wrapped) > room:
            queue[0] = " ".join(wrapped[room:])
        else:
            queue.pop(0)
    return lines


def mark_truncated(lines: list[str], width: int) -> list[str]:
    """
    End a block of lines with the "continued in ICID" note, cutting the last line to make room.
    Takes the lines and the characters per line.
    Returns the lines with the note on the last one.
    """
    room = width - len(TEXT_OVERFLOW)
    return lines[:-1] + [lines[-1][:room].rstrip() + TEXT_OVERFLOW]


def write_lines(workbook: WorkbookTemplate, sheet: str, rows: range, lines: list[str], column: str = "B") -> None:
    """
    Write lines onto a block of ruled lines, left-aligned, blanking the rest of the block.
    Takes the workbook, sheet name, the rows, the lines and the column the lines start in (B unless given).
    Returns nothing.
    """
    for index, row in enumerate(rows):
        workbook.set_cell(sheet, f"{column}{row}", lines[index] if index < len(lines) else None)
        workbook.align_left(sheet, f"{column}{row}")


# ---- Pay items --------------------------------------------------------------

def pay_item_rows(pay_items: Any, capacity: int) -> list[dict[str, Optional[str]]]:
    """
    Lay pay items out for a form's table: the unit folded into Pay Quantity, Quantity Chk left for the RE.
    Takes the report's payItems (anything that isn't a list counts as none) and the table's row count.
    Returns at most one dict per table row; when there are more items than rows, the last row says how many more.
    """
    items = [item for item in pay_items if isinstance(item, dict)] if isinstance(pay_items, list) else []
    rows = []
    for item in items:
        quantity = " ".join(part for part in (text_value(item.get("payQuantity")), text_value(item.get("unit"))) if part)
        rows.append({
            "itemNo": text_value(item.get("itemNo")),
            "budgetCode": text_value(item.get("budgetCode")),
            "payQuantity": quantity or None,
            "quantityChk": None,
            "description": text_value(item.get("description")),
        })
    if len(rows) > capacity:
        more = len(rows) - (capacity - 1)
        rows = rows[: capacity - 1] + [{"itemNo": None, "budgetCode": None, "payQuantity": None, "quantityChk": None,
                                        "description": f"… {more} more items in ICID"}]
    return rows


def truncate_to_lines(text: str, width: int, max_lines: int, suffix: str) -> str:
    """
    Cut text so that it, plus a suffix, wraps into at most max_lines lines, preferring to end on a whole word.
    Takes the text, the characters per line, the number of lines and the suffix (e.g. "...").
    Returns the cut text ending in the suffix; a cut backs up to a space when one is within 10 characters.
    """
    limit = width * max_lines - len(suffix)
    while limit > 0:
        cut = text[:limit]
        space = cut.rfind(" ")
        if space > 0 and space >= limit - 10:
            cut = cut[:space]
        candidate = cut.rstrip(" ,;:-") + suffix
        if len(textwrap.wrap(candidate, width)) <= max_lines:
            return candidate
        limit = (space if space > 0 else limit) - 1
    return suffix


def fit_pay_description(description: str, layout: PayItemsLayout) -> tuple[str, int, int]:
    """
    Fit a pay item's description into its cell's two lines: at the template's 10 pt, else shrunk to 8 pt, else cut.
    Takes the description text and the table's layout (its characters per line at each size).
    Returns (the text to write, how many lines it takes, the font size); cut text ends in "..." at a word boundary.
    """
    for font_size, width in ((PAY_DESCRIPTION_FONT_PT, layout.line_chars_10pt),
                             (PAY_DESCRIPTION_SHRINK_FONT_PT, layout.line_chars_8pt)):
        lines = textwrap.wrap(description, width)
        if len(lines) <= PAY_DESCRIPTION_MAX_LINES:
            return description, len(lines), font_size
    text = truncate_to_lines(description, layout.line_chars_8pt, PAY_DESCRIPTION_MAX_LINES,
                             PAY_DESCRIPTION_TRUNCATE_SUFFIX)
    return text, PAY_DESCRIPTION_MAX_LINES, PAY_DESCRIPTION_SHRINK_FONT_PT


def stamp_pay_items(workbook: WorkbookTemplate, sheet: str, layout: PayItemsLayout, pay_items: Any) -> None:
    """
    Write the pay items into a form's table, blanking unused rows. Descriptions wrap, and a row whose description
    needs two lines gets a row tall enough for them (lines x the font's line height).
    Takes the workbook, sheet name, the table's layout and the report's payItems.
    Returns nothing.
    """
    rows = pay_item_rows(pay_items, len(layout.rows))
    for index, row in enumerate(layout.rows):
        values = rows[index] if index < len(rows) else {}
        for field, column in layout.columns.items():
            workbook.set_cell(sheet, f"{column}{row}", values.get(field))
        description = values.get("description")
        if description:
            cell = f"{layout.columns['description']}{row}"
            text, lines, font_size = fit_pay_description(description, layout)
            workbook.set_cell(sheet, cell, text)
            workbook.wrap_cell(sheet, cell)
            if font_size != PAY_DESCRIPTION_FONT_PT:
                workbook.set_font_size(sheet, cell, font_size)
            if lines > 1:
                workbook.set_row_height(sheet, row, lines * LINE_HEIGHT[font_size])


# ---- Back pages: work force, equipment, MPT/safety checklist ----------------

# Older reports used singular workforce keys; the frontend renames them to plurals on load
LEGACY_WORKFORCE_KEYS = {"foreman": "foremen", "operator": "operators", "flagger": "flaggers"}

# The frontend's standard equipment keys and their names (a form without a row for one exports it like added equipment)
STANDARD_EQUIPMENT_LABELS = {"frontEndLoader": "Front End Loader", "backhoe": "Backhoe", "truckDump": "Truck (Dump)",
                             "compressor": "Compressor", "excavator": "Excavator", "pavementCutter": "Pavement Cutter"}


@dataclass(frozen=True)
class WorkforceLayout:
    """Where one form's Work Force table is."""

    role_rows: dict[str, int]       # frontend workforce key -> row
    trade_rows: dict[str, int]      # added trade label (lower-case) -> its pre-printed row
    free_rows: tuple[int, ...]      # blank rows, label written in
    label_column: str
    count_column: str


@dataclass(frozen=True)
class EquipmentLayout:
    """Where one form's Equipment table is."""

    standard_rows: dict[str, int]   # frontend equipment key -> row
    extra_rows: dict[str, int]      # added equipment label (lower-case) -> its pre-printed row
    row_names: frozenset[str]       # labels (lower-case) that are exactly a pre-printed row's name
    free_rows: tuple[int, ...]      # blank rows, label written in
    label_column: str
    slots: tuple[tuple[str, str], ...]  # each row's (Model / Size, No.) column pairs


@dataclass(frozen=True)
class SafetyLayout:
    """Where one form's End of the Day MPT/Safety Check List is (Y and N boxes; no form has an N/A column)."""

    rows: dict[str, int]            # frontend safety checklist key -> row
    yes_column: str
    no_column: str
    remarks_column: str


def count_value(value: Any) -> Any:
    """
    Turn a headcount or equipment number as typed (usually a string) into what the form's No. cell should show.
    Takes the value.
    Returns an int for a whole number, the trimmed text otherwise, or None when blank.
    """
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    return int(number) if number == number.to_integral_value() else text


def _place_extras(entries: list[dict[str, Any]], preprinted: dict[str, int],
                  free_rows: tuple[int, ...], slots_per_row: int) -> tuple[list[tuple[int, int, dict[str, Any], bool]], int]:
    """
    Decide where added rows (extra trades or equipment) go: on the form's pre-printed row for their label when there
    is one with room, otherwise on a blank row with their label written in.
    Takes the entries ({label, ...}), label -> pre-printed row, the blank rows, and how many entries one row can take.
    Returns ([(row, slot, entry, needs_label)], how many entries didn't fit).
    """
    used: dict[int, int] = {}
    placed, unplaced = [], []
    for entry in entries:
        row = preprinted.get(str(entry.get("label") or "").strip().lower())
        if row is not None and used.get(row, 0) < slots_per_row:
            placed.append((row, used.get(row, 0), entry, False))
            used[row] = used.get(row, 0) + 1
        else:
            unplaced.append(entry)
    for row, entry in zip(free_rows, unplaced):
        placed.append((row, 0, entry, True))
    return placed, max(0, len(unplaced) - len(free_rows))


def _overflow_label(placed: list[tuple[int, int, dict[str, Any], bool]], free_rows: tuple[int, ...], missing: int) -> list:
    """
    Give up the last blank row to a "+N more" note when added rows didn't all fit.
    Takes the placements, the blank rows and how many entries didn't fit.
    Returns the placements without the one on the last blank row (whose entry joins the count).
    """
    if not missing:
        return placed
    return [p for p in placed if not (p[3] and p[0] == free_rows[-1])]


def stamp_workforce(workbook: WorkbookTemplate, sheet: str, layout: WorkforceLayout, data: dict[str, Any]) -> bool:
    """
    Write headcounts: the standard roles, then added trades on their pre-printed rows or, label written in, blank rows.
    Takes the workbook, sheet name, the table's layout and the report_data.
    Returns whether any headcount or trade was written.
    """
    saved = data.get("workforce") if isinstance(data.get("workforce"), dict) else {}
    workforce = {LEGACY_WORKFORCE_KEYS.get(key, key): value for key, value in saved.items()}
    wrote = False
    for role, row in layout.role_rows.items():
        count = count_value(workforce.get(role))
        workbook.set_cell(sheet, f"{layout.count_column}{row}", count)
        wrote = wrote or count is not None

    extras = [e for e in data.get("additionalWorkforce") or [] if isinstance(e, dict)]
    placed, missing = _place_extras(extras, layout.trade_rows, layout.free_rows, 1)
    placed = _overflow_label(placed, layout.free_rows, missing)
    for row, _, entry, needs_label in placed:
        if needs_label:
            workbook.set_cell(sheet, f"{layout.label_column}{row}", text_value(entry.get("label")))
        workbook.set_cell(sheet, f"{layout.count_column}{row}", count_value(entry.get("count")))
        wrote = True
    if missing:
        workbook.set_cell(sheet, f"{layout.label_column}{layout.free_rows[-1]}", f"+{missing + 1} more (see ICID)")
    return wrote


def stamp_equipment(workbook: WorkbookTemplate, sheet: str, layout: EquipmentLayout, data: dict[str, Any]) -> bool:
    """
    Write equipment: model / size and number for the standard types the form has a row for, then added equipment (and
    any standard type it has no row for) on its pre-printed row, using either of the row's Model / No. pairs, or on a
    blank row with its label written in.
    Takes the workbook, sheet name, the table's layout and the report_data.
    Returns whether anything was written.
    """
    equipment = data.get("equipment") if isinstance(data.get("equipment"), dict) else {}
    model_column, number_column = layout.slots[0]
    wrote = False
    without_row = []
    for key, label in STANDARD_EQUIPMENT_LABELS.items():
        entry = equipment.get(key) if isinstance(equipment.get(key), dict) else {}
        model, number = text_value(entry.get("model")), count_value(entry.get("number"))
        if key in layout.standard_rows:
            row = layout.standard_rows[key]
            workbook.set_cell(sheet, f"{model_column}{row}", model)
            workbook.set_cell(sheet, f"{number_column}{row}", number)
            wrote = wrote or model is not None or number is not None
        elif model is not None or number is not None:
            without_row.append({"label": label, "model": entry.get("model"), "number": entry.get("number")})

    extras = without_row + [e for e in data.get("additionalEquipment") or [] if isinstance(e, dict)]
    placed, missing = _place_extras(extras, layout.extra_rows, layout.free_rows, len(layout.slots))
    placed = _overflow_label(placed, layout.free_rows, missing)
    for row, slot, entry, needs_label in placed:
        label = text_value(entry.get("label"))
        model = text_value(entry.get("model"))
        if needs_label:
            workbook.set_cell(sheet, f"{layout.label_column}{row}", label)
        elif label and label.lower() not in layout.row_names:
            # A variant on a shared pre-printed row (Roller - Dynamic on Gen Bk's Roller) keeps its name by the model
            model = " ".join(part for part in (label, model) if part)
        model_column, number_column = layout.slots[slot]
        workbook.set_cell(sheet, f"{model_column}{row}", model)
        workbook.set_cell(sheet, f"{number_column}{row}", count_value(entry.get("number")))
        wrote = True
    if missing:
        workbook.set_cell(sheet, f"{layout.label_column}{layout.free_rows[-1]}", f"+{missing + 1} more (see ICID)")
    return wrote


def _safety_answer(value: Any) -> Optional[str]:
    """
    Normalise a safety checklist answer, including older reports' booleans.
    Takes the saved value.
    Returns 'Y', 'N', 'NA', or None when unanswered.
    """
    if value is True:
        return "Y"
    if value is False:
        return "N"
    return value if value in ("Y", "N", "NA") else None


def stamp_safety(workbook: WorkbookTemplate, sheet: str, layout: SafetyLayout, data: dict[str, Any]) -> bool:
    """
    Write the MPT/safety checklist: an X under Y or N, and the remarks. The forms have no N/A column, so an N/A answer
    leaves both boxes empty and is written at the start of the remarks instead.
    Takes the workbook, sheet name, the checklist's layout and the report_data.
    Returns whether any answer or remark was written.
    """
    checks = data.get("safetyChecks") if isinstance(data.get("safetyChecks"), dict) else {}
    remarks = data.get("safetyRemarks") if isinstance(data.get("safetyRemarks"), dict) else {}
    wrote = False
    for key, row in layout.rows.items():
        answer = _safety_answer(checks.get(key))
        remark = text_value(remarks.get(key))
        if answer == "NA":
            remark = f"N/A — {remark}" if remark else "N/A"
        workbook.set_cell(sheet, f"{layout.yes_column}{row}", CHECK_MARK if answer == "Y" else None)
        workbook.set_cell(sheet, f"{layout.no_column}{row}", CHECK_MARK if answer == "N" else None)
        workbook.set_cell(sheet, f"{layout.remarks_column}{row}", remark)
        wrote = wrote or answer is not None or remark is not None
    return wrote


# ---- Long text: front page -> back page -> Report Cont ----------------------
#
# A report's Description of Work fills its front page's ruled lines; what doesn't fit continues on the back page's
# remark lines under "Description of work (continued):", followed by the comments ("Comments:" when both are there).
# What doesn't fit there continues on the Report Cont sheet, and what doesn't fit on Report Cont is cut with a note
# pointing to ICID. The template has one Report Cont, so only one report per export can continue onto it; a report
# that can't has its back page's last line cut instead.

REPORT_CONT = "Report Cont"
REPORT_CONT_DAY_CELLS = ("I11", "J11", "K11", "L11", "M11", "N11", "O11")  # S M T W T F S
REPORT_CONT_PROJECT_CELLS = {"G14": "project_id", "P14": "registration_code", "I15": "project_description",
                             "F17": "borough"}


@dataclass(frozen=True)
class TextArea:
    """A block of ruled lines: its rows, the column the text goes in, and the characters a line holds."""

    rows: range
    column: str
    line_chars: int


REPORT_CONT_TEXT = TextArea(rows=range(21, 46), column="B", line_chars=85)  # B21 … B45, 10 pt lines spanning B:AI


@dataclass(frozen=True)
class TextFlow:
    """Where a report's description and comments landed."""

    front: list[str]
    back: list[str]
    report_cont: list[str]
    past_front: bool   # the description continued past the front page
    past_back: bool    # the text continued past the back page (onto Report Cont, or cut when it isn't available)


def flow_text(description: Any, comments: Any, front: TextArea, back: TextArea,
              report_cont: Optional[TextArea]) -> TextFlow:
    """
    Lay a report's description and comments out across its front page, back page and (when available) Report Cont.
    Takes the description and comments (non-text counts as empty), the front and back areas, and Report Cont's area,
    or None when another report already uses Report Cont.
    Returns a TextFlow with each area's lines; text past the last available area is cut with the "continued" note.
    """
    queue = paragraphs(description)
    front_lines = fill_lines(queue, len(front.rows), front.line_chars)
    past_front = bool(queue)
    remarks = paragraphs(comments)
    continuation = []
    if queue:
        continuation += ["Description of work (continued):"] + queue
        if remarks:
            continuation.append("Comments:")
    continuation += remarks
    back_lines = fill_lines(continuation, len(back.rows), back.line_chars)
    past_back = bool(continuation)
    cont_lines: list[str] = []
    if continuation and report_cont is None:
        back_lines = mark_truncated(back_lines, back.line_chars)
    elif continuation:
        cont_lines = fill_lines(continuation, len(report_cont.rows), report_cont.line_chars)
        if continuation:
            cont_lines = mark_truncated(cont_lines, report_cont.line_chars)
    return TextFlow(front_lines, back_lines, cont_lines, past_front, past_back)


def stamp_report_cont(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                      inspector: Optional[str], lines: list[str]) -> None:
    """
    Fill the Report Cont sheet: its header (date, day, project details as values, inspector; its blank-line
    placeholders cleared) and the continued text on its ruled lines.
    Takes the workbook, IDR row, project details, inspector name and the lines.
    Returns nothing.
    """
    workbook.set_cell(REPORT_CONT, "I10", short_date(idr["report_date"]))
    highlight_day(workbook, REPORT_CONT, REPORT_CONT_DAY_CELLS, idr["report_date"])
    workbook.set_cell(REPORT_CONT, "U10", None)  # I.R. No. "__________"
    workbook.set_cell(REPORT_CONT, "AA10", "Sheet No.:")  # was "Sheet No.: ____ of ____"
    for cell, field in REPORT_CONT_PROJECT_CELLS.items():
        workbook.set_cell(REPORT_CONT, cell, project.get(field))
    workbook.set_cell(REPORT_CONT, "H19", inspector)
    write_lines(workbook, REPORT_CONT, REPORT_CONT_TEXT.rows, lines, REPORT_CONT_TEXT.column)


# ---- Draft marker -----------------------------------------------------------

# A draft IDR exports with this across the top of every printed page. Row 1 is an empty strip merged across the page
# on every form (B1:AP1 on the fronts, B1:AI1 on the backs and Report Cont), above the logos and headers, and inside
# the print area; it is raised from 14.25 pt so the 14 pt text fits.
DRAFT_MARKER = "DRAFT - Not for Submission"
DRAFT_MARKER_CELL = "B1"
DRAFT_MARKER_FONT_PT = 14
DRAFT_MARKER_COLOR = "FFFF0000"  # red
DRAFT_MARKER_ROW_HEIGHT = 18.75


def stamp_draft_marker(workbook: WorkbookTemplate, sheet: str) -> None:
    """
    Mark a sheet as a draft: "DRAFT - Not for Submission" in red bold 14 pt, centred across its top row.
    Takes the workbook and sheet name.
    Returns nothing.
    """
    workbook.set_cell(sheet, DRAFT_MARKER_CELL, DRAFT_MARKER)
    style = workbook.cell_style(sheet, DRAFT_MARKER_CELL)
    workbook.set_style(sheet, DRAFT_MARKER_CELL,
                       workbook.font_style(style, points=DRAFT_MARKER_FONT_PT, bold=True, rgb=DRAFT_MARKER_COLOR))
    workbook.center_cell(sheet, DRAFT_MARKER_CELL)
    workbook.set_row_height(sheet, 1, DRAFT_MARKER_ROW_HEIGHT)
