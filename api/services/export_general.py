"""
Stamps an IDR's General onto the DDC template's General pages.

Gen Fr is the front: header, Description of Work and Pay Items. Gen Bk is the back: comments,
the MPT/safety checklist, work force and equipment. Report Cont takes whatever text doesn't fit.

Text flows in a cascade. The description fills Gen Fr's ruled lines; what doesn't fit continues on
Gen Bk's comment lines (Gen Fr's "Reverse page used" box ticked), followed by the comments. What
doesn't fit there continues on Report Cont (Gen Bk's "Continued on next page" box ticked), and what
doesn't fit on Report Cont is cut with a note pointing to ICID.

Cell positions come from reading templates/report_forms.xlsx; see the constants below.
"""

import textwrap
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from api.services.xlsx_template import WorkbookTemplate

GEN_FRONT = "Gen Fr"
GEN_BACK = "Gen Bk"
REPORT_CONT = "Report Cont"

CHECK_MARK = "X"
TEXT_OVERFLOW = " … (continued in ICID)"

# ---- Gen Fr -----------------------------------------------------------------

DAY_OF_WEEK_CELLS = ["AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5"]  # S M T W T F S

# Header cells that are formulas reading Contract Info in the template; the export writes the values in directly
GEN_FRONT_PROJECT_CELLS = {"G8": "project_id", "P8": "registration_code", "I10": "project_description",
                           "F12": "borough", "F14": "contractor"}

DESCRIPTION_ROWS = range(22, 35)  # B22:AP22 … B34:AP34, size-14 ruled lines
DESCRIPTION_LINE_CHARS = 60
REVERSE_PAGE_BOX = "AC36"  # "Reverse page used for additional remarks."

PAY_ITEM_ROWS = range(39, 51)  # under the header at row 38; row 51 is the form's footer
PAY_ITEM_COLUMNS = {"itemNo": "B", "budgetCode": "G", "payQuantity": "N", "quantityChk": "S", "description": "X"}

# ---- Gen Bk -----------------------------------------------------------------

COMMENT_ROWS = range(3, 7)  # B3:AI3 … B6:AI6, size-14 ruled lines; rows 7-25 are the sketch grid
COMMENT_LINE_CHARS = 60
CONTINUED_BOX = "Z27"  # "Continued on next page (if any)."

SAFETY_ROWS = {  # the frontend's safety checklist keys, in the form's row order
    "plasticBarrels": 30, "pedestrianBarricades": 31, "timberCurbs": 32, "timberBreakawayBarricades": 33,
    "generalSafety": 34, "localEmergencyAccess": 35, "fencing": 36, "plates": 37, "arrowBoard": 38,
    "siteCleaned": 39,
}
SAFETY_YES_COLUMN, SAFETY_NO_COLUMN, SAFETY_REMARKS_COLUMN = "N", "P", "R"  # the form has no N/A column

WORKFORCE_ROWS = {"superintendent": 43, "foremen": 44, "operators": 45, "laborers": 46, "flaggers": 47}
LEGACY_WORKFORCE_KEYS = {"foreman": "foremen", "operator": "operators", "flagger": "flaggers"}
TRADE_ROWS = {"carpenters": 48, "timbermen": 49, "masons": 50, "teamsters": 51}  # pre-printed on the form
FREE_TRADE_ROWS = [52, 53]
TRADE_LABEL_COLUMN, TRADE_COUNT_COLUMN = "B", "G"

EQUIPMENT_ROWS = {"frontEndLoader": 43, "backhoe": 44, "truckDump": 46, "compressor": 48, "excavator": 49}
# Added equipment the form has a pre-printed row for (by the frontend's labels, lower-cased)
EQUIPMENT_EXTRA_ROWS = {"crane": 45, "roller": 47, "roller – static": 47, "roller – dynamic": 47, "trailers": 50,
                        "pavement cutter": 51, "tampers": 52, "hand tamper": 52}
EQUIPMENT_ROW_NAMES = {"crane", "roller", "trailers", "pavement cutter", "tampers"}  # as printed on those rows
FREE_EQUIPMENT_ROWS = [53]
EQUIPMENT_LABEL_COLUMN = "I"
EQUIPMENT_SLOTS = [("N", "V"), ("Y", "AG")]  # each row has two Model / Size + No. pairs

# ---- Report Cont ------------------------------------------------------------

REPORT_CONT_ROWS = range(21, 46)  # B21 … B45, size-10 ruled lines spanning B:AI
REPORT_CONT_LINE_CHARS = 85
REPORT_CONT_DAY_CELLS = ["I11", "J11", "K11", "L11", "M11", "N11", "O11"]  # S M T W T F S
REPORT_CONT_PROJECT_CELLS = {"G14": "project_id", "P14": "registration_code", "I15": "project_description",
                             "F17": "borough"}


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


def _write_lines(workbook: WorkbookTemplate, sheet: str, rows: range, lines: list[str]) -> None:
    """
    Write lines onto a block of ruled lines, left-aligned, blanking the rest of the block.
    Takes the workbook, sheet name, the rows (column B) and the lines.
    Returns nothing.
    """
    for index, row in enumerate(rows):
        workbook.set_cell(sheet, f"B{row}", lines[index] if index < len(lines) else None)
        workbook.align_left(sheet, f"B{row}")


# ---- Value formatting -------------------------------------------------------

def _time_range(start: Optional[time], end: Optional[time]) -> Optional[str]:
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


def _temperature(label: str, value: Optional[Decimal]) -> str:
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


def _count(value: Any) -> Any:
    """
    Turn a headcount as typed (usually a string) into what the form's No. cell should show.
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


def _text(value: Any) -> Optional[str]:
    """
    Normalise a free-text field for a cell.
    Takes the value.
    Returns the trimmed string, or None when blank or missing.
    """
    text = str(value).strip() if value is not None else ""
    return text or None


def _short_date(day: date) -> str:
    """
    Format a date the way Gen Fr's date cell shows it (m/d/yy), for cells that hold it as text.
    Takes the date.
    Returns e.g. "9/30/26".
    """
    return f"{day.month}/{day.day}/{day:%y}"


def _highlight_day(workbook: WorkbookTemplate, sheet: str, cells: list[str], day: date) -> None:
    """
    Mark the report's day of the week among a form's S M T W T F S letters.
    Takes the workbook, sheet, the seven letter cells (Sunday first) and the date.
    Returns nothing.
    """
    cell = cells[(day.weekday() + 1) % 7]
    workbook.set_style(sheet, cell, workbook.highlighted_style(workbook.cell_style(sheet, cell)))


# ---- Gen Fr ------------------------------------------------------------------

def _stamp_front_header(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                        inspector: Optional[str], page_number: Optional[int]) -> None:
    """
    Write Gen Fr's header: project details (as values, not Contract Info formulas), date, day, times, temperatures,
    weather, sheet number and inspector.
    Takes the workbook, IDR row, project details (with "contractor"), inspector name and the General's page number.
    Returns nothing; I.R. No. stays blank (no source yet), and Sheet No. is blank for a composed General.
    """
    for cell, field in GEN_FRONT_PROJECT_CELLS.items():
        workbook.set_cell(GEN_FRONT, cell, project.get(field))
    workbook.set_cell(GEN_FRONT, "AI4", idr["report_date"])
    _highlight_day(workbook, GEN_FRONT, DAY_OF_WEEK_CELLS, idr["report_date"])
    workbook.set_cell(GEN_FRONT, "AH6", None)
    has_page = page_number is not None
    workbook.set_cell(GEN_FRONT, "AH8", page_number if has_page else None)
    workbook.set_cell(GEN_FRONT, "AM8", idr.get("total_pages") if has_page else None)
    workbook.set_cell(GEN_FRONT, "AG10", _time_range(idr.get("work_start_time"), idr.get("work_end_time")))
    workbook.set_cell(GEN_FRONT, "AG12", _time_range(idr.get("inspector_start_time"), idr.get("inspector_end_time")))
    workbook.set_cell(GEN_FRONT, "AD13", _temperature("Low", idr.get("temp_low")))
    workbook.set_cell(GEN_FRONT, "AK13", _temperature("High", idr.get("temp_high")))
    workbook.set_cell(GEN_FRONT, "AD17", _text(idr.get("weather_am")))
    workbook.set_cell(GEN_FRONT, "AK17", _text(idr.get("weather_pm")))
    workbook.set_cell(GEN_FRONT, "H17", inspector)


def pay_item_rows(pay_items: Any) -> list[dict[str, Optional[str]]]:
    """
    Lay pay items out for Gen Fr's table: the unit folded into Pay Quantity, Quantity Chk left for the RE.
    Takes the report's payItems (anything that isn't a list counts as none).
    Returns at most one dict per table row; when there are more items than rows, the last row says how many more.
    """
    items = [item for item in pay_items if isinstance(item, dict)] if isinstance(pay_items, list) else []
    rows = []
    for item in items:
        quantity = " ".join(part for part in (_text(item.get("payQuantity")), _text(item.get("unit"))) if part)
        rows.append({
            "itemNo": _text(item.get("itemNo")),
            "budgetCode": _text(item.get("budgetCode")),
            "payQuantity": quantity or None,
            "quantityChk": None,
            "description": _text(item.get("description")),
        })
    capacity = len(PAY_ITEM_ROWS)
    if len(rows) > capacity:
        more = len(rows) - (capacity - 1)
        rows = rows[: capacity - 1] + [{"itemNo": None, "budgetCode": None, "payQuantity": None, "quantityChk": None,
                                        "description": f"… {more} more items in ICID"}]
    return rows


def _stamp_pay_items(workbook: WorkbookTemplate, pay_items: Any) -> None:
    """
    Write the pay items into Gen Fr's table, blanking unused rows.
    Takes the workbook and the report's payItems.
    Returns nothing.
    """
    rows = pay_item_rows(pay_items)
    for index, row in enumerate(PAY_ITEM_ROWS):
        values = rows[index] if index < len(rows) else {}
        for field, column in PAY_ITEM_COLUMNS.items():
            workbook.set_cell(GEN_FRONT, f"{column}{row}", values.get(field))


# ---- Gen Bk ------------------------------------------------------------------

def _place_extras(entries: list[dict[str, Any]], preprinted: dict[str, int],
                  free_rows: list[int], slots_per_row: int) -> tuple[list[tuple[int, int, dict[str, Any], bool]], int]:
    """
    Decide where added rows (extra trades or equipment) go: on the form's pre-printed row for their label when there
    is one with room, otherwise on a blank row with their label written in.
    Takes the entries ({label, ...}), label → pre-printed row, the blank rows, and how many entries one row can take.
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


def _overflow_label(placed: list[tuple[int, int, dict[str, Any], bool]], free_rows: list[int], missing: int) -> list:
    """
    Give up the last blank row to a "+N more" note when added rows didn't all fit.
    Takes the placements, the blank rows and how many entries didn't fit.
    Returns the placements without the one on the last blank row (whose entry joins the count).
    """
    if not missing:
        return placed
    return [p for p in placed if not (p[3] and p[0] == free_rows[-1])]


def _stamp_workforce(workbook: WorkbookTemplate, data: dict[str, Any]) -> bool:
    """
    Write headcounts: the five standard roles, then added trades on their pre-printed or blank rows.
    Takes the workbook and the General's report_data.
    Returns whether any headcount or trade was written.
    """
    saved = data.get("workforce") if isinstance(data.get("workforce"), dict) else {}
    workforce = {LEGACY_WORKFORCE_KEYS.get(key, key): value for key, value in saved.items()}
    wrote = False
    for role, row in WORKFORCE_ROWS.items():
        count = _count(workforce.get(role))
        workbook.set_cell(GEN_BACK, f"{TRADE_COUNT_COLUMN}{row}", count)
        wrote = wrote or count is not None

    extras = [e for e in data.get("additionalWorkforce") or [] if isinstance(e, dict)]
    placed, missing = _place_extras(extras, TRADE_ROWS, FREE_TRADE_ROWS, 1)
    placed = _overflow_label(placed, FREE_TRADE_ROWS, missing)
    for row, _, entry, needs_label in placed:
        if needs_label:
            workbook.set_cell(GEN_BACK, f"{TRADE_LABEL_COLUMN}{row}", _text(entry.get("label")))
        workbook.set_cell(GEN_BACK, f"{TRADE_COUNT_COLUMN}{row}", _count(entry.get("count")))
        wrote = True
    if missing:
        workbook.set_cell(GEN_BACK, f"{TRADE_LABEL_COLUMN}{FREE_TRADE_ROWS[-1]}", f"+{missing + 1} more (see ICID)")
    return wrote


def _stamp_equipment(workbook: WorkbookTemplate, data: dict[str, Any]) -> bool:
    """
    Write equipment: model / size and number for the five standard types, then added equipment on its pre-printed
    row (either of the row's two Model / No. pairs) or a blank row.
    Takes the workbook and the General's report_data.
    Returns whether anything was written.
    """
    equipment = data.get("equipment") if isinstance(data.get("equipment"), dict) else {}
    model_column, number_column = EQUIPMENT_SLOTS[0]
    wrote = False
    for key, row in EQUIPMENT_ROWS.items():
        entry = equipment.get(key) if isinstance(equipment.get(key), dict) else {}
        model, number = _text(entry.get("model")), _count(entry.get("number"))
        workbook.set_cell(GEN_BACK, f"{model_column}{row}", model)
        workbook.set_cell(GEN_BACK, f"{number_column}{row}", number)
        wrote = wrote or model is not None or number is not None

    extras = [e for e in data.get("additionalEquipment") or [] if isinstance(e, dict)]
    placed, missing = _place_extras(extras, EQUIPMENT_EXTRA_ROWS, FREE_EQUIPMENT_ROWS, len(EQUIPMENT_SLOTS))
    placed = _overflow_label(placed, FREE_EQUIPMENT_ROWS, missing)
    for row, slot, entry, needs_label in placed:
        label = _text(entry.get("label"))
        model = _text(entry.get("model"))
        if needs_label:
            workbook.set_cell(GEN_BACK, f"{EQUIPMENT_LABEL_COLUMN}{row}", label)
        elif label and label.lower() not in EQUIPMENT_ROW_NAMES:
            # A variant on a pre-printed row (Roller – Dynamic on Roller) keeps its own name next to the model
            model = " ".join(part for part in (label, model) if part)
        model_column, number_column = EQUIPMENT_SLOTS[slot]
        workbook.set_cell(GEN_BACK, f"{model_column}{row}", model)
        workbook.set_cell(GEN_BACK, f"{number_column}{row}", _count(entry.get("number")))
        wrote = True
    if missing:
        workbook.set_cell(GEN_BACK, f"{EQUIPMENT_LABEL_COLUMN}{FREE_EQUIPMENT_ROWS[-1]}", f"+{missing + 1} more (see ICID)")
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


def _stamp_safety(workbook: WorkbookTemplate, data: dict[str, Any]) -> bool:
    """
    Write the MPT/safety checklist: an X under Y or N, and the remarks. The form has no N/A column, so an N/A answer
    is written at the start of the remarks instead.
    Takes the workbook and the General's report_data.
    Returns whether any answer or remark was written.
    """
    checks = data.get("safetyChecks") if isinstance(data.get("safetyChecks"), dict) else {}
    remarks = data.get("safetyRemarks") if isinstance(data.get("safetyRemarks"), dict) else {}
    wrote = False
    for key, row in SAFETY_ROWS.items():
        answer = _safety_answer(checks.get(key))
        remark = _text(remarks.get(key))
        if answer == "NA":
            remark = f"N/A — {remark}" if remark else "N/A"
        workbook.set_cell(GEN_BACK, f"{SAFETY_YES_COLUMN}{row}", CHECK_MARK if answer == "Y" else None)
        workbook.set_cell(GEN_BACK, f"{SAFETY_NO_COLUMN}{row}", CHECK_MARK if answer == "N" else None)
        workbook.set_cell(GEN_BACK, f"{SAFETY_REMARKS_COLUMN}{row}", remark)
        wrote = wrote or answer is not None or remark is not None
    return wrote


# ---- Report Cont ------------------------------------------------------------

def _stamp_report_cont_header(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                              inspector: Optional[str]) -> None:
    """
    Write Report Cont's header: date, day, project details (as values) and inspector, clearing its blank-line
    placeholders.
    Takes the workbook, IDR row, project details and inspector name.
    Returns nothing.
    """
    workbook.set_cell(REPORT_CONT, "I10", _short_date(idr["report_date"]))
    _highlight_day(workbook, REPORT_CONT, REPORT_CONT_DAY_CELLS, idr["report_date"])
    workbook.set_cell(REPORT_CONT, "U10", None)  # I.R. No. "__________"
    workbook.set_cell(REPORT_CONT, "AA10", "Sheet No.:")  # was "Sheet No.: ____ of ____"
    for cell, field in REPORT_CONT_PROJECT_CELLS.items():
        workbook.set_cell(REPORT_CONT, cell, project.get(field))
    workbook.set_cell(REPORT_CONT, "H19", inspector)


# ---- The General ------------------------------------------------------------

def stamp_general(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any], inspector: Optional[str],
                  general_data: dict[str, Any], page_number: Optional[int]) -> list[str]:
    """
    Stamp the General onto Gen Fr, Gen Bk and (for long text) Report Cont.
    Takes the workbook, IDR row, project details (with "contractor"), inspector name, the General's report_data
    and its page number (None for a composed General).
    Returns the sheets that now hold content, in print order: Gen Fr always, Gen Bk and Report Cont when used.
    """
    _stamp_front_header(workbook, idr, project, inspector, page_number)
    _stamp_pay_items(workbook, general_data.get("payItems"))

    description = paragraphs(general_data.get("description"))
    _write_lines(workbook, GEN_FRONT, DESCRIPTION_ROWS, fill_lines(description, len(DESCRIPTION_ROWS), DESCRIPTION_LINE_CHARS))

    # What continues past Gen Fr: the rest of the description, then the comments
    comments = paragraphs(general_data.get("comments"))
    continuation = []
    if description:
        continuation += ["Description of work (continued):"] + description
        if comments:
            continuation.append("Comments:")
    continuation += comments
    workbook.set_cell(GEN_FRONT, REVERSE_PAGE_BOX, CHECK_MARK if description else None)

    back_lines = fill_lines(continuation, len(COMMENT_ROWS), COMMENT_LINE_CHARS)
    _write_lines(workbook, GEN_BACK, COMMENT_ROWS, back_lines)
    workbook.set_cell(GEN_BACK, CONTINUED_BOX, CHECK_MARK if continuation else None)

    back_used = bool(back_lines)
    back_used = _stamp_workforce(workbook, general_data) or back_used
    back_used = _stamp_equipment(workbook, general_data) or back_used
    back_used = _stamp_safety(workbook, general_data) or back_used

    sheets = [GEN_FRONT] + ([GEN_BACK] if back_used else [])
    if continuation:
        cont_lines = fill_lines(continuation, len(REPORT_CONT_ROWS), REPORT_CONT_LINE_CHARS)
        if continuation:
            cont_lines = mark_truncated(cont_lines, REPORT_CONT_LINE_CHARS)
        _stamp_report_cont_header(workbook, idr, project, inspector)
        _write_lines(workbook, REPORT_CONT, REPORT_CONT_ROWS, cont_lines)
        sheets.append(REPORT_CONT)
    return sheets
