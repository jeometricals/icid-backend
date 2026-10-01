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

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from api.services.export_common import (
    CHECK_MARK, HeaderLayout, PayItemsLayout, fill_lines, highlight_day, mark_truncated, paragraphs, short_date,
    stamp_common_header, stamp_pay_items, text_value, write_lines,
)
from api.services.xlsx_template import WorkbookTemplate

GEN_FRONT = "Gen Fr"
GEN_BACK = "Gen Bk"
REPORT_CONT = "Report Cont"

# ---- Gen Fr -----------------------------------------------------------------

GEN_FRONT_HEADER = HeaderLayout(
    project_cells={"G8": "project_id", "P8": "registration_code", "I10": "project_description",
                   "F12": "borough", "F14": "contractor"},
    date="AI4", date_as_text=False,  # a merged cell formatted m/d/yy
    day_of_week=("AI5", "AJ5", "AK5", "AL5", "AM5", "AN5", "AO5"),
    ir_no="AH6", sheet_no="AH8", sheet_of="AM8",
    work_time="AG10", inspector_time="AG12",
    temp_low="AD13", temp_high="AK13",
    weather_am="AD17", weather_pm="AK17", weather_labels=None,  # value boxes under separate "AM" / "PM" labels
    inspector="H17",
)

DESCRIPTION_ROWS = range(22, 35)  # B22:AP22 … B34:AP34, size-14 ruled lines
DESCRIPTION_LINE_CHARS = 60
REVERSE_PAGE_BOX = "AC36"  # "Reverse page used for additional remarks."

# Pay Items: rows 39-50 under the header at row 38 (row 51 is the form's footer). The Description cell (X:AP,
# merged, centred Arial 10) is about 293 px wide inside its padding: measured Arial fits ~46 characters a line at
# 10 pt and ~55 at 8 pt; the budgets keep a safety margin (a 43-character item was seen clipping in a viewer).
GEN_FRONT_PAY_ITEMS = PayItemsLayout(
    rows=range(39, 51),
    columns={"itemNo": "B", "budgetCode": "G", "payQuantity": "N", "quantityChk": "S", "description": "X"},
    line_chars_10pt=40, line_chars_8pt=48,
)

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
REPORT_CONT_DAY_CELLS = ("I11", "J11", "K11", "L11", "M11", "N11", "O11")  # S M T W T F S
REPORT_CONT_PROJECT_CELLS = {"G14": "project_id", "P14": "registration_code", "I15": "project_description",
                             "F17": "borough"}


# ---- Value formatting -------------------------------------------------------

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


# ---- Gen Fr ------------------------------------------------------------------

def _stamp_front_header(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                        inspector: Optional[str], page_number: Optional[int]) -> None:
    """
    Write Gen Fr's header block (see stamp_common_header).
    Takes the workbook, IDR row, project details (with "contractor"), inspector name and the General's page number.
    Returns nothing; Sheet No. is blank for a composed General, which isn't one of the IDR's numbered pages.
    """
    stamp_common_header(workbook, GEN_FRONT, GEN_FRONT_HEADER, idr, project, project.get("contractor"),
                        inspector, page_number)


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
            workbook.set_cell(GEN_BACK, f"{TRADE_LABEL_COLUMN}{row}", text_value(entry.get("label")))
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
        model, number = text_value(entry.get("model")), _count(entry.get("number"))
        workbook.set_cell(GEN_BACK, f"{model_column}{row}", model)
        workbook.set_cell(GEN_BACK, f"{number_column}{row}", number)
        wrote = wrote or model is not None or number is not None

    extras = [e for e in data.get("additionalEquipment") or [] if isinstance(e, dict)]
    placed, missing = _place_extras(extras, EQUIPMENT_EXTRA_ROWS, FREE_EQUIPMENT_ROWS, len(EQUIPMENT_SLOTS))
    placed = _overflow_label(placed, FREE_EQUIPMENT_ROWS, missing)
    for row, slot, entry, needs_label in placed:
        label = text_value(entry.get("label"))
        model = text_value(entry.get("model"))
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
        remark = text_value(remarks.get(key))
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
    workbook.set_cell(REPORT_CONT, "I10", short_date(idr["report_date"]))
    highlight_day(workbook, REPORT_CONT, REPORT_CONT_DAY_CELLS, idr["report_date"])
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
    stamp_pay_items(workbook, GEN_FRONT, GEN_FRONT_PAY_ITEMS, general_data.get("payItems"))

    description = paragraphs(general_data.get("description"))
    write_lines(workbook, GEN_FRONT, DESCRIPTION_ROWS, fill_lines(description, len(DESCRIPTION_ROWS), DESCRIPTION_LINE_CHARS))

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
    write_lines(workbook, GEN_BACK, COMMENT_ROWS, back_lines)
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
        write_lines(workbook, REPORT_CONT, REPORT_CONT_ROWS, cont_lines)
        sheets.append(REPORT_CONT)
    return sheets
