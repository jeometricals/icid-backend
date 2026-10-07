"""
What the DDC report forms share: the IDR header block (project details, date and day, I.R. No., sheet number, times,
temperatures, weather and inspector), the Pay Items table, laying free text out on ruled lines, and the value
formatting used to fill them.

Each form puts these in its own cells; a HeaderLayout or PayItemsLayout names them for one sheet, and the stamp_*
functions fill them. Per-report modules (export_general, export_swcb, ...) own their sheets' layouts.
"""

import io
import re
import textwrap
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image

from api.services.export_redlines import NO_REDLINES, RedlineEntry, Redlines, redline_chain
from api.services.xlsx_template import EMU_PER_PIXEL, TextRun, WorkbookTemplate

CHECK_MARK = "X"
# A drawn checkbox rectangle (8-10 px, transparent since the template cleanup) is ticked with a small centred X
BOX_MARK_FONT_PT = 6
TEXT_OVERFLOW = " … (continued in ICID)"

# A pay item's description gets at most two lines: at the template's 10 pt, then shrunk to 8 pt, then cut with "...".
# Its rows are fixed-height (one line), and Excel won't grow rows holding merged cells, so two lines get a taller row.
PAY_DESCRIPTION_FONT_PT = 10
PAY_DESCRIPTION_SHRINK_FONT_PT = 8
PAY_DESCRIPTION_MAX_LINES = 2
PAY_DESCRIPTION_TRUNCATE_SUFFIX = "..."
LINE_HEIGHT = {10: 12.75, 8: 11.25}  # points per line of Arial at that size (Excel's default row heights)

# A report with more pay items than its table holds continues on copies of its front page
PAY_ITEMS_CONTINUED_NEXT = "Pay items continued on next page"
PAY_ITEMS_CONTINUED_FROM = "Pay items continued from previous page"


# How the forms abbreviate a pay unit, keyed by the catalog's unit with its periods and spaces dropped, in capitals
# (so "L.F." and "LF" are one unit). A unit that isn't listed prints in that same form, cut to four characters.
PAY_UNIT_ABBREVIATIONS = {"LF": "LF", "SF": "SF", "CY": "CY", "SY": "SY", "TON": "TN", "EACH": "EA"}
PAY_UNIT_MAX_CHARS = 4
# The unit is printed after the quantity, smaller, whatever size the quantity cell's own font is: its size in points,
# and whether it is raised as a superscript (which Excel draws smaller still) or sits on the number's baseline
PAY_UNIT_FONT_PT = 8
PAY_UNIT_SUPERSCRIPT = True

# A reviewer's edit prints in this blue (ARGB), with their initials after it as a small label (the header's cells
# are 8 pt). A pay item's initials go in its row's Quantity Chk cell instead, at the larger size. A value a later one replaced is struck
# through. The inspector's value after the last edit closes a chain in black, labelled "(revised)" in small grey italic.
PAY_REDLINE_COLOR = "FF0070C0"
PAY_REDLINE_INITIALS_FONT_PT = 8
REDLINE_INITIALS_FONT_PT = 6
REDLINE_REVISED_LABEL = "(revised)"
REDLINE_REVISED_COLOR = "FF808080"
REDLINE_REVISED_FONT_PT = 6


@dataclass(frozen=True)
class PayItemsLayout:
    """Where one form's Pay Items table is, and how many description characters fit a line at 10 pt and 8 pt."""

    rows: range
    columns: dict[str, str]  # itemNo, budgetCode, payQuantity, quantityChk, description -> column letter
    line_chars_10pt: int
    line_chars_8pt: int


@dataclass(frozen=True)
class TextArea:
    """A block of ruled lines: its rows, the column the text goes in, and the characters a line holds."""

    rows: range
    column: str
    line_chars: int


@dataclass(frozen=True)
class ContinuationHeader:
    """Where a continuation form's header fields go (Report Cont, Sketch Cont): no times, weather or contractor."""

    project_cells: dict[str, str]   # cell -> project field; formulas reading Contract Info there are replaced
    date: str                       # General-formatted: the date goes in as m/d/yy text
    day_of_week: tuple[str, ...]    # S M T W T F S, Sunday first
    ir_no: str                      # "__________", replaced by the IDR's number (cleared when it has none)
    sheet_no: str                   # "Sheet No.: ____ of ____", left as its label
    inspector: str


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
    # Where a form is laid out for a pen (Conc Fr, AC Fr): the date line is seven one-column cells with a "/" drawn in
    # two of them, and the I.R. No. cell is one column in a 6 pt row. A typed value needs the room Gen Fr gives it, so
    # these areas are merged before it is written (the date and I.R. No. cells are their top-left cells) ...
    merge_areas: tuple[str, ...] = ()
    # ... and these cells lose their drawn "/": they take the date cell's style, which keeps the line under them
    date_slashes: tuple[str, ...] = ()


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


def object_rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """
    Read one list-valued section of report_data (a table's rows), tolerating a missing list or malformed rows.
    Takes the report_data and the section's key.
    Returns the rows that are objects, in the inspector's order.
    """
    rows = data.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def typed_value(value: Any) -> Any:
    """
    Keep a value as the frontend saved it, for a cell: a number stays a number, text is trimmed.
    Takes the value.
    Returns the int / float, the trimmed text, or None when blank or missing.
    """
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return value
    return text_value(value)


def ir_number(idr: dict[str, Any]) -> Optional[str]:
    """
    Give what a page's "I.R. No." takes: the IDR's number, as the reviewer typed it at Stage 1.
    Takes the IDR row.
    Returns the number as text (so "005" keeps its zeros), or None for an IDR nobody has accepted yet.
    """
    return text_value(idr.get("idr_number")) or None


def short_date(day: date) -> str:
    """
    Format a date the way the forms' date-formatted cells show it (m/d/yy), for cells that hold it as text.
    Takes the date.
    Returns e.g. "9/30/26".
    """
    return f"{day.month}/{day.day}/{day:%y}"


TIME_BLANK = "________"


def time_text(value: Any) -> Optional[str]:
    """
    Format a header time for the form.
    Takes the time, as the IDR holds it or as an edit logged it (ISO text like "07:00:00").
    Returns HH:MM, or None when there is none (or the text isn't a time).
    """
    if isinstance(value, str):
        try:
            value = time.fromisoformat(value.strip())
        except ValueError:
            return None
    return value.strftime("%H:%M") if isinstance(value, time) else None


def time_range(start: Optional[time], end: Optional[time]) -> Optional[str]:
    """
    Fill the template's "( Start ___ End ___ )" line.
    Takes the start and end times (either may be None).
    Returns the line with HH:MM for each known time, or None (a blank cell) when neither is known.
    """
    if start is None and end is None:
        return None
    return f"( Start {time_text(start) or TIME_BLANK} End {time_text(end) or TIME_BLANK} )"


def temperature_text(value: Any) -> Optional[str]:
    """
    Format a temperature for the form.
    Takes the temperature, as the IDR holds it or as an edit logged it (a number, or one as text).
    Returns it without needless decimals ("45", "62.5"), or None when there is none (or it isn't a number).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return str(number.quantize(Decimal(1))) if number == number.to_integral_value() else str(number.normalize())


def temperature(label: str, value: Optional[Decimal]) -> str:
    """
    Fill a Low / High temperature box, whose label shares the cell with the value.
    Takes the label and the temperature (or None).
    Returns e.g. "Low  45" (whole numbers without decimals), or just the label when unknown.
    """
    shown = temperature_text(value)
    return f"{label}  {shown}" if shown is not None else label


# ---- Reviewer redlines --------------------------------------------------------

def redline_runs(entries: list[RedlineEntry]) -> list[TextRun]:
    """
    Turn a field's chain into the runs of its cell, on one line: each value in its colour, struck when replaced, a
    reviewer's followed by their initials as a small label, and the inspector's later value by "(revised)".
    Takes the chain (see export_redlines.redline_chain).
    Returns the runs, in order (none for an empty chain).
    """
    runs: list[TextRun] = []
    for entry in entries:
        if runs:
            runs.append(TextRun(" "))
        if entry.by_reviewer:
            runs.append(TextRun(entry.text, color=PAY_REDLINE_COLOR, strike=entry.struck))
            if entry.initials:
                runs.append(TextRun(f" {entry.initials}", color=PAY_REDLINE_COLOR, points=REDLINE_INITIALS_FONT_PT))
        else:
            runs.append(TextRun(entry.text, strike=entry.struck))
        if entry.revised:
            runs.append(TextRun(f" {REDLINE_REVISED_LABEL}", color=REDLINE_REVISED_COLOR,
                                points=REDLINE_REVISED_FONT_PT, italic=True))
    return runs


def stamp_field(workbook: WorkbookTemplate, sheet: str, cell: str, current: Any, edits: list[dict[str, Any]],
                show: Optional[Callable[[Any], Any]] = None, prefix: Optional[str] = None) -> None:
    """
    Write one field's value into its cell, or its chain when reviewers edited it: on one line, shrunk to fit.
    Takes the workbook, sheet, cell, the field's current value, its edits (oldest first), the function that formats a
    value for the cell (text_value unless given) and text to print before it (a name sharing the cell).
    Returns nothing.
    """
    show = show or text_value
    if not edits:
        shown = show(current)
        workbook.set_cell(sheet, cell, (" ".join(part for part in (prefix, shown) if part) or None) if prefix else shown)
        return
    runs = redline_runs(redline_chain(edits, current, show))
    workbook.set_cell_runs(sheet, cell, ([TextRun(f"{prefix} ")] if prefix else []) + runs)
    workbook.shrink_on_one_line(sheet, cell)


def highlight_day(workbook: WorkbookTemplate, sheet: str, cells: tuple[str, ...], day: date) -> None:
    """
    Mark the report's day of the week among a form's S M T W T F S letters (the form circles it by hand).
    Takes the workbook, sheet, the seven letter cells (Sunday first) and the date.
    Returns nothing.
    """
    cell = cells[(day.weekday() + 1) % 7]
    workbook.set_style(sheet, cell, workbook.highlighted_style(workbook.cell_style(sheet, cell)))


def _weather(workbook: WorkbookTemplate, sheet: str, cell: str, label: Optional[str], value: Any,
             edits: list[dict[str, Any]]) -> None:
    """
    Write one weather box: the value alone, or under the box's own label when the two share the cell. An edited
    value prints as its chain on one line shrunk to fit, after the label where the box has one: no box is tall
    enough to stack the values (Gen Fr's is 21 pt), and a cell that wraps can't shrink.
    Takes the workbook, sheet, cell, the shared label (None when the box holds only the value), the weather and its
    edits (oldest first).
    Returns nothing; a shared box gets "AM" and the value on separate lines, so it wraps.
    """
    entries = redline_chain(edits, value)
    if entries:
        workbook.set_cell_runs(sheet, cell, ([TextRun(f"{label}  ")] if label else []) + redline_runs(entries))
        workbook.shrink_on_one_line(sheet, cell)
        return
    weather = text_value(value)
    if label is None:
        workbook.set_cell(sheet, cell, weather)
        return
    workbook.set_cell(sheet, cell, f"{label}\n{weather}" if weather else label)
    if weather:
        workbook.wrap_cell(sheet, cell)


def _stamp_time_range(workbook: WorkbookTemplate, sheet: str, cell: str, idr: dict[str, Any], start_column: str,
                      end_column: str, redlines: Redlines) -> None:
    """
    Write a "( Start ___ End ___ )" line, each time as its chain when reviewers edited it.
    Takes the workbook, sheet, cell, the IDR row, its start and end columns and the IDR's redlines.
    Returns nothing; a line with a chain goes on one line shrunk to fit.
    """
    chains = [redline_chain(redlines.header(column), idr.get(column), time_text) for column in (start_column, end_column)]
    if not any(chains):
        workbook.set_cell(sheet, cell, time_range(idr.get(start_column), idr.get(end_column)))
        return
    runs = []
    for lead, column, chain in zip(("( Start ", " End "), (start_column, end_column), chains):
        runs += [TextRun(lead)] + (redline_runs(chain) or [TextRun(time_text(idr.get(column)) or TIME_BLANK)])
    workbook.set_cell_runs(sheet, cell, runs + [TextRun(" )")])
    workbook.shrink_on_one_line(sheet, cell)


def _stamp_temperature(workbook: WorkbookTemplate, sheet: str, cell: str, label: str, idr: dict[str, Any],
                       column: str, redlines: Redlines) -> None:
    """
    Write a Low / High temperature box: its label, then the value, or its chain when reviewers edited it.
    Takes the workbook, sheet, cell, the label, the IDR row, the temperature's column and the IDR's redlines.
    Returns nothing; a box with a chain goes on one line shrunk to fit.
    """
    chain = redline_chain(redlines.header(column), idr.get(column), temperature_text)
    if not chain:
        workbook.set_cell(sheet, cell, temperature(label, idr.get(column)))
        return
    workbook.set_cell_runs(sheet, cell, [TextRun(f"{label}  ")] + redline_runs(chain))
    workbook.shrink_on_one_line(sheet, cell)


def stamp_common_header(workbook: WorkbookTemplate, sheet: str, layout: HeaderLayout, idr: dict[str, Any],
                        project: dict[str, Any], contractor: Optional[str], inspector: Optional[str],
                        page_number: Optional[int], redlines: Redlines = NO_REDLINES) -> None:
    """
    Write a form's header block: project details (as values, not Contract Info formulas), date, day, times,
    temperatures, weather, sheet number and inspector, clearing the template's placeholders when a value is missing.
    A time, temperature or weather a reviewer edited prints as its chain. Where the form's date and I.R. No. cells are
    too small for a typed value, they are first merged into the areas the layout names.
    Takes the workbook, sheet name, its layout, the IDR row, the project row, the contractor's and inspector's names,
    this page's number (None leaves Sheet No. blank, e.g. for a page that isn't one of the IDR's numbered pages) and
    the IDR's redlines (none unless given).
    Returns nothing; I.R. No. takes the IDR's number, and stays blank for an IDR without one.
    """
    for cell, field in layout.project_cells.items():
        workbook.set_cell(sheet, cell, contractor if field == "contractor" else project.get(field))
    for area in layout.merge_areas:
        workbook.ensure_merged(sheet, area)
    for cell in layout.date_slashes:
        workbook.set_style(sheet, cell, workbook.cell_style(sheet, layout.date))
    report_date: date = idr["report_date"]
    workbook.set_cell(sheet, layout.date, short_date(report_date) if layout.date_as_text else report_date)
    highlight_day(workbook, sheet, layout.day_of_week, report_date)
    workbook.set_cell(sheet, layout.ir_no, ir_number(idr))
    has_page = page_number is not None
    workbook.set_cell(sheet, layout.sheet_no, page_number if has_page else None)
    workbook.set_cell(sheet, layout.sheet_of, idr.get("total_pages") if has_page else None)
    _stamp_time_range(workbook, sheet, layout.work_time, idr, "work_start_time", "work_end_time", redlines)
    _stamp_time_range(workbook, sheet, layout.inspector_time, idr, "inspector_start_time", "inspector_end_time",
                      redlines)
    _stamp_temperature(workbook, sheet, layout.temp_low, "Low", idr, "temp_low", redlines)
    _stamp_temperature(workbook, sheet, layout.temp_high, "High", idr, "temp_high", redlines)
    am_label, pm_label = layout.weather_labels or (None, None)
    _weather(workbook, sheet, layout.weather_am, am_label, idr.get("weather_am"), redlines.header("weather_am"))
    _weather(workbook, sheet, layout.weather_pm, pm_label, idr.get("weather_pm"), redlines.header("weather_pm"))
    workbook.set_cell(sheet, layout.inspector, inspector)


# ---- Text layout ------------------------------------------------------------

class RedlineText(str):
    """A paragraph, or one line of it, from a text field's chain: the text, and how the chain marks it."""

    struck: bool
    by_reviewer: bool
    tail: Optional[str]  # the small label after the paragraph's last line: the reviewer's initials, or "(revised)"

    def __new__(cls, text: str, struck: bool = False, by_reviewer: bool = False,
                tail: Optional[str] = None) -> "RedlineText":
        """
        Mark a piece of text.
        Takes the text, whether it is struck, whether it is a reviewer's and the label that follows it (or None).
        Returns the marked text.
        """
        marked = super().__new__(cls, text)
        marked.struck, marked.by_reviewer, marked.tail = struck, by_reviewer, tail
        return marked

    def like(self, text: str, tail: Optional[str] = None) -> "RedlineText":
        """
        Mark other text the way this is marked (a line of this paragraph, or what is left of it).
        Takes the text and the label that follows it (None for a line that isn't the paragraph's last).
        Returns the marked text.
        """
        return RedlineText(text, self.struck, self.by_reviewer, tail)


def redline_paragraphs(text: Any, edits: list[dict[str, Any]]) -> list[str]:
    """
    Split a free-text field into the paragraphs the form prints: its own, or, when reviewers edited it, every value
    of its chain in turn, the replaced ones struck and each reviewer's ending in their initials.
    Takes the field's current text and its edits (oldest first).
    Returns the paragraphs; those of an edited field are RedlineText.
    """
    if not edits:
        return paragraphs(text)
    marked: list[str] = []
    for entry in redline_chain(edits, text, lambda value: "\n".join(paragraphs(value)) or None):
        tail = REDLINE_REVISED_LABEL if entry.revised else entry.initials
        parts = entry.text.splitlines()
        marked += [RedlineText(part, entry.struck, entry.by_reviewer, tail if index == len(parts) - 1 else None)
                   for index, part in enumerate(parts)]
    return marked


def _wrap(paragraph: str, width: int) -> list[str]:
    """
    Wrap one paragraph into lines, leaving room on its last line for the label that follows it.
    Takes the paragraph (plain, or RedlineText) and the characters per line.
    Returns its lines; a RedlineText's lines are marked like it, the last one carrying its label.
    """
    if not isinstance(paragraph, RedlineText):
        return textwrap.wrap(paragraph, width)
    if not paragraph.tail:
        return [paragraph.like(line) for line in textwrap.wrap(paragraph, width)]
    lines = textwrap.wrap(f"{paragraph} {paragraph.tail}", width)
    last = lines[-1][: -len(paragraph.tail)].rstrip()
    return [paragraph.like(line) for line in lines[:-1]] + [paragraph.like(last, paragraph.tail)]


def redline_text_runs(line: RedlineText) -> list[TextRun]:
    """
    Turn one line of a text field's chain into its cell's runs.
    Takes the line.
    Returns the text in its colour, struck when replaced, then its label when it ends a paragraph: a reviewer's
    initials in the redline colour, or "(revised)" in small grey italic.
    """
    color = PAY_REDLINE_COLOR if line.by_reviewer else None
    runs = [TextRun(str(line), color=color, strike=line.struck)]
    if line.tail and line.by_reviewer:
        runs.append(TextRun(f" {line.tail}", color=color, points=REDLINE_INITIALS_FONT_PT))
    elif line.tail:
        runs.append(TextRun(f" {line.tail}", color=REDLINE_REVISED_COLOR, points=REDLINE_REVISED_FONT_PT, italic=True))
    return runs


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
        wrapped = _wrap(queue[0], width)
        room = capacity - len(lines)
        lines.extend(wrapped[:room])
        if len(wrapped) > room:
            rest = " ".join(wrapped[room:])
            queue[0] = queue[0].like(rest, queue[0].tail) if isinstance(queue[0], RedlineText) else rest
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
    last = lines[-1][:room].rstrip() + TEXT_OVERFLOW
    return lines[:-1] + [lines[-1].like(last) if isinstance(lines[-1], RedlineText) else last]


def write_lines(workbook: WorkbookTemplate, sheet: str, rows: range, lines: list[str], column: str = "B") -> None:
    """
    Write lines onto a block of ruled lines, left-aligned, blanking the rest of the block. A line of an edited
    field's chain (RedlineText) is written with its marks.
    Takes the workbook, sheet name, the rows, the lines and the column the lines start in (B unless given).
    Returns nothing.
    """
    for index, row in enumerate(rows):
        line = lines[index] if index < len(lines) else None
        if isinstance(line, RedlineText):
            workbook.set_cell_runs(sheet, f"{column}{row}", redline_text_runs(line))
        else:
            workbook.set_cell(sheet, f"{column}{row}", line)
        workbook.align_left(sheet, f"{column}{row}")


# ---- Pay items --------------------------------------------------------------

def _pay_item_list(pay_items: Any) -> list[dict[str, Any]]:
    """
    Read a report's payItems, tolerating a missing list or malformed entries.
    Takes the saved payItems (anything that isn't a list counts as none).
    Returns the pay items that are objects, in the inspector's order.
    """
    return [item for item in pay_items if isinstance(item, dict)] if isinstance(pay_items, list) else []


def pay_unit_abbreviation(unit: Any) -> Optional[str]:
    """
    Shorten a pay unit for the form: "L.F." -> "LF", "Ton" -> "TN", "Each" -> "EA".
    Takes the unit as saved with the pay item (the catalog's pay_unit, or anything else).
    Returns the abbreviation; for a unit that isn't in PAY_UNIT_ABBREVIATIONS, the unit without periods and spaces,
    in capitals, cut to PAY_UNIT_MAX_CHARS; None when there is no unit.
    """
    plain = re.sub(r"[.\s]", "", text_value(unit) or "").upper()
    if not plain:
        return None
    return PAY_UNIT_ABBREVIATIONS.get(plain, plain[:PAY_UNIT_MAX_CHARS])


def pay_item_group(item: dict[str, Any], redlines: Redlines = NO_REDLINES,
                   capacity: Optional[int] = None) -> list[dict[str, Any]]:
    """
    Lay one pay item out as the rows it prints on: the quantity as entered, its unit abbreviated beside it, and in
    Quantity Chk the initials of the reviewers who added, revised or approved that row's quantity ("AD / RM / MK";
    empty for a row no reviewer initialled). An item without a quantity has no unit either, so its Pay Quantity cell
    stays empty. An item a reviewer revised takes a row more per revision, under its own (see Redlines.pay_item);
    those rows repeat its item number and budget code and leave the description to the first.
    Takes the pay item, the report's redlines (none unless given) and the table's row count, when the rows must fit
    a page that also holds a "continued" note: an item with more keeps its own row and its latest revisions.
    Returns the rows, each with "redline": its QuantityLine, or None for a row no reviewer marked.
    """
    lines = redlines.pay_item(item) or [None]
    if capacity is not None and len(lines) > capacity - 1:
        lines = lines[:1] + lines[-(capacity - 2):]
    rows = []
    for line in lines:
        quantity = line.quantity if line is not None else (text_value(item.get("payQuantity")) or None)
        rows.append({
            "itemNo": text_value(item.get("itemNo")),
            "budgetCode": text_value(item.get("budgetCode")),
            "payQuantity": quantity,
            "unit": pay_unit_abbreviation(item.get("unit")) if quantity else None,
            "quantityChk": (" / ".join(line.initials) or None) if line is not None else None,
            "description": text_value(item.get("description")) if line is None or line.item_row else None,
            "redline": line,
        })
    return rows


def pay_item_rows(pay_items: Any, capacity: int, continued: bool = False,
                  redlines: Redlines = NO_REDLINES) -> list[dict[str, Any]]:
    """
    Lay pay items out for a form's table (see pay_item_group).
    Takes the sheet's payItems (anything that isn't a list counts as none), the table's row count, whether the
    items continue on another sheet (the last row then says so instead of holding an item) and the report's
    redlines (none unless given).
    Returns at most one dict per table row.
    """
    rows = [row for item in _pay_item_list(pay_items) for row in pay_item_group(item, redlines, capacity)]
    rows = rows[: capacity - 1 if continued else capacity]
    if continued:
        rows.append({"itemNo": None, "budgetCode": None, "payQuantity": None, "unit": None, "quantityChk": None,
                     "description": PAY_ITEMS_CONTINUED_NEXT, "redline": None})
    return rows


def pay_item_slices(pay_items: Any, capacity: int, redlines: Redlines = NO_REDLINES) -> list[list[dict[str, Any]]]:
    """
    Split a report's pay items across its front pages: every page but the last keeps its table's last row for the
    "continued on next page" note, so holds one row fewer; the last uses every row. An item a reviewer revised
    takes several rows (see pay_item_group), and they stay on one page.
    Takes the report's payItems, the table's row count and the report's redlines (none unless given).
    Returns one list of items per page, always at least one (empty when there are no items).
    """
    items = _pay_item_list(pay_items)
    sizes = [len(pay_item_group(item, redlines, capacity)) for item in items]
    slices, start = [], 0
    while sum(sizes[start:]) > capacity:
        end, used = start, 0
        while used + sizes[end] <= capacity - 1:
            used += sizes[end]
            end += 1
        slices.append(items[start:end])
        start = end
    return slices + [items[start:]]


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


def _stamp_pay_quantity(workbook: WorkbookTemplate, sheet: str, cell: str, values: dict[str, Any]) -> None:
    """
    Write one row's Pay Quantity cell: the quantity, its unit after it, smaller (PAY_UNIT_FONT_PT,
    PAY_UNIT_SUPERSCRIPT), and what a reviewer's mark adds: the quantity struck when a revision replaced it, a
    reviewer's quantity and unit in the redline colour, and "(revised)" after a quantity the inspector changed
    since. Initials never go here: they have the Quantity Chk cell.
    Takes the workbook, sheet, cell and the row (see pay_item_group).
    Returns nothing; the cell shrinks to fit, so a long number is scaled down rather than cut off.
    """
    quantity, unit, line = values.get("payQuantity"), values.get("unit"), values.get("redline")
    if line is None:
        if not quantity:
            return
        if unit:
            workbook.set_cell_with_suffix(sheet, cell, quantity, unit, PAY_UNIT_FONT_PT, superscript=PAY_UNIT_SUPERSCRIPT)
        workbook.shrink_to_fit_cell(sheet, cell)
        return
    color = PAY_REDLINE_COLOR if line.by_reviewer else None
    runs = [TextRun(quantity or "", color=color, strike=line.struck),
            TextRun(unit or "", color=color, points=PAY_UNIT_FONT_PT, superscript=PAY_UNIT_SUPERSCRIPT, font="Arial")]
    if line.revised:
        runs.append(TextRun(f" {REDLINE_REVISED_LABEL}", color=REDLINE_REVISED_COLOR,
                            points=REDLINE_REVISED_FONT_PT, italic=True))
    workbook.set_cell_runs(sheet, cell, runs)
    workbook.shrink_to_fit_cell(sheet, cell)


def stamp_pay_items(workbook: WorkbookTemplate, sheet: str, layout: PayItemsLayout, pay_items: Any,
                    continued: bool = False, redlines: Redlines = NO_REDLINES) -> None:
    """
    Write the pay items into a form's table, blanking unused rows. Descriptions wrap, and a row whose description
    needs two lines gets a row tall enough for them (lines x the font's line height). A quantity is followed by its
    unit, smaller. The Item No. and Pay Quantity cells shrink to fit, so a wide code or a long number is scaled down
    rather than cut off. A reviewer's marks print with the items (see pay_item_group and _stamp_pay_quantity): a
    reviewer's row (a revision's, or an item a reviewer added) has every cell it fills in the redline colour, the
    description of an added item included, and the Quantity Chk cell holds the row's initials in the redline colour
    (PAY_REDLINE_INITIALS_FONT_PT), shrunk to fit.
    Takes the workbook, sheet name, the table's layout, the sheet's payItems (no more than fit), whether they
    continue on another sheet (the last row then says "Pay items continued on next page") and the report's redlines
    (none unless given).
    Returns nothing.
    """
    rows = pay_item_rows(pay_items, len(layout.rows), continued, redlines)
    for index, row in enumerate(layout.rows):
        values = rows[index] if index < len(rows) else {}
        line = values.get("redline")
        for field, column in layout.columns.items():
            workbook.set_cell(sheet, f"{column}{row}", values.get(field))
        if values.get("itemNo"):
            workbook.shrink_to_fit_cell(sheet, f"{layout.columns['itemNo']}{row}")
        by_reviewer = line is not None and line.by_reviewer
        if by_reviewer:
            for field in ("itemNo", "budgetCode"):
                workbook.set_font_color(sheet, f"{layout.columns[field]}{row}", PAY_REDLINE_COLOR)
        _stamp_pay_quantity(workbook, sheet, f"{layout.columns['payQuantity']}{row}", values)
        if values.get("quantityChk"):
            cell = f"{layout.columns['quantityChk']}{row}"
            workbook.set_cell_runs(sheet, cell, [TextRun(values["quantityChk"], color=PAY_REDLINE_COLOR,
                                                         points=PAY_REDLINE_INITIALS_FONT_PT)])
            workbook.shrink_to_fit_cell(sheet, cell)
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
            if by_reviewer:
                workbook.set_font_color(sheet, cell, PAY_REDLINE_COLOR)


def pay_item_page_count(pay_items: Any, layout: PayItemsLayout, redlines: Redlines = NO_REDLINES) -> int:
    """
    Count the front pages a report's pay items need (see pay_item_slices).
    Takes the report's payItems, the form's pay-item layout and the report's redlines (none unless given).
    Returns the number of pages, at least one.
    """
    return len(pay_item_slices(pay_items, len(layout.rows), redlines))


def copy_names(base: str, count: int, first_index: int = 0) -> list[str]:
    """
    Name a run of copies of a template sheet, numbered across the IDR: the template's own sheet, then "<base> 2", ...
    Takes the template sheet's name, how many sheets and the run's first position (0 for the template's own sheet).
    Returns the names, in order.
    """
    return [base if index == 0 else f"{base} {index + 1}" for index in range(first_index, first_index + count)]


def allocate_copies(workbook: WorkbookTemplate, base: str, count: int, first_index: int = 0) -> list[str]:
    """
    Provide a run of a template sheet's copies (a report's front pages, its Conc Mix sheets, ...), cloning the blank
    template sheet for each name past its own. Call it before anything is stamped on the template sheet.
    Takes the workbook, the template sheet's name, how many sheets and the run's first position (see copy_names).
    Returns the sheet names, in order.
    """
    names = copy_names(base, count, first_index)
    for name in names:
        if name != base:
            workbook.clone_sheet(base, name)
    return names


def stamp_pay_item_pages(workbook: WorkbookTemplate, fronts: list[str], header: HeaderLayout, layout: PayItemsLayout,
                         text: TextArea, idr: dict[str, Any], project: dict[str, Any], contractor: Optional[str],
                         inspector: Optional[str], page_number: Optional[int], pay_items: Any,
                         redlines: Redlines = NO_REDLINES) -> None:
    """
    Write a report's pay items across its front pages, and give each overflow page its header (the next page number
    on from the report's) and "Pay items continued from previous page" on its first text line. The caller stamps
    the first page's header and text; overflow pages' other sections stay blank.
    Takes the workbook, the front pages, the form's header, pay-item and text layouts, the IDR row, the project row,
    the contractor's and inspector's names, the report's page number (None leaves Sheet No. blank), its payItems and
    its redlines (none unless given).
    Returns nothing; raises ValueError when the pages given don't match what the items need.
    """
    slices = pay_item_slices(pay_items, len(layout.rows), redlines)
    if len(fronts) != len(slices):
        count = len(_pay_item_list(pay_items))
        raise ValueError(f"{count} pay items need {len(slices)} front pages, got {len(fronts)}")
    for index, (sheet, items) in enumerate(zip(fronts, slices)):
        stamp_pay_items(workbook, sheet, layout, items, continued=index < len(fronts) - 1, redlines=redlines)
        if index:
            page = page_number + index if page_number is not None else None
            stamp_common_header(workbook, sheet, header, idr, project, contractor, inspector, page, redlines)
            write_lines(workbook, sheet, text.rows, [PAY_ITEMS_CONTINUED_FROM], text.column)


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
    remarks_column: Optional[str]   # None where the form has no remarks column (AC Bk)


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


def rows_with_paths(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """
    Read a list of added rows (extra trades or equipment), each with the field_path an edit names it by.
    Takes the report_data and the list's key, e.g. "additionalWorkforce".
    Returns the rows that are objects, each with "_path" ("additionalWorkforce[0]", by its place in the saved list).
    """
    rows = data.get(key) if isinstance(data.get(key), list) else []
    return [{**row, "_path": f"{key}[{index}]"} for index, row in enumerate(rows) if isinstance(row, dict)]


def entry_edits(redlines: Redlines, entry: dict[str, Any], field: str) -> list[dict[str, Any]]:
    """
    Find the edits made to one field of an added row.
    Takes the report's redlines, the row (see rows_with_paths) and the field's key, e.g. "count".
    Returns its edits, oldest first (empty for a row without a path, or a field never edited).
    """
    return redlines.field(f"{entry['_path']}.{field}") if entry.get("_path") else []


def stamp_workforce(workbook: WorkbookTemplate, sheet: str, layout: WorkforceLayout, data: dict[str, Any],
                    redlines: Redlines = NO_REDLINES) -> bool:
    """
    Write headcounts: the standard roles, then added trades on their pre-printed rows or, label written in, blank rows.
    A count or a written-in label a reviewer edited prints as its chain.
    Takes the workbook, sheet name, the table's layout, the report_data and the report's redlines (none unless given).
    Returns whether any headcount or trade was written.
    """
    saved = data.get("workforce") if isinstance(data.get("workforce"), dict) else {}
    stored = {LEGACY_WORKFORCE_KEYS.get(key, key): key for key in saved}  # role -> the key report_data has it under
    wrote = False
    for role, row in layout.role_rows.items():
        key = stored.get(role, role)
        edits = redlines.field(f"workforce.{key}")
        stamp_field(workbook, sheet, f"{layout.count_column}{row}", saved.get(key), edits, count_value)
        wrote = wrote or count_value(saved.get(key)) is not None or bool(edits)

    extras = rows_with_paths(data, "additionalWorkforce")
    placed, missing = _place_extras(extras, layout.trade_rows, layout.free_rows, 1)
    placed = _overflow_label(placed, layout.free_rows, missing)
    for row, _, entry, needs_label in placed:
        if needs_label:
            stamp_field(workbook, sheet, f"{layout.label_column}{row}", entry.get("label"),
                        entry_edits(redlines, entry, "label"))
        stamp_field(workbook, sheet, f"{layout.count_column}{row}", entry.get("count"),
                    entry_edits(redlines, entry, "count"), count_value)
        wrote = True
    if missing:
        workbook.set_cell(sheet, f"{layout.label_column}{layout.free_rows[-1]}", f"+{missing + 1} more (see ICID)")
    return wrote


def stamp_equipment(workbook: WorkbookTemplate, sheet: str, layout: EquipmentLayout, data: dict[str, Any],
                    redlines: Redlines = NO_REDLINES) -> bool:
    """
    Write equipment: model / size and number for the standard types the form has a row for, then added equipment (and
    any standard type it has no row for) on its pre-printed row, using either of the row's Model / No. pairs, or on a
    blank row with its label written in. A model, number or written-in label a reviewer edited prints as its chain.
    Takes the workbook, sheet name, the table's layout, the report_data and the report's redlines (none unless given).
    Returns whether anything was written.
    """
    equipment = data.get("equipment") if isinstance(data.get("equipment"), dict) else {}
    model_column, number_column = layout.slots[0]
    wrote = False
    without_row = []
    for key, label in STANDARD_EQUIPMENT_LABELS.items():
        entry = equipment.get(key) if isinstance(equipment.get(key), dict) else {}
        model, number = text_value(entry.get("model")), count_value(entry.get("number"))
        path = f"equipment.{key}"
        edited = bool(redlines.field(f"{path}.model") or redlines.field(f"{path}.number"))
        if key in layout.standard_rows:
            row = layout.standard_rows[key]
            stamp_field(workbook, sheet, f"{model_column}{row}", entry.get("model"), redlines.field(f"{path}.model"))
            stamp_field(workbook, sheet, f"{number_column}{row}", entry.get("number"),
                        redlines.field(f"{path}.number"), count_value)
            wrote = wrote or model is not None or number is not None or edited
        elif model is not None or number is not None or edited:
            without_row.append({"label": label, "model": entry.get("model"), "number": entry.get("number"),
                                "_path": path, "_standard": True})

    extras = without_row + rows_with_paths(data, "additionalEquipment")
    placed, missing = _place_extras(extras, layout.extra_rows, layout.free_rows, len(layout.slots))
    placed = _overflow_label(placed, layout.free_rows, missing)
    for row, slot, entry, needs_label in placed:
        label = text_value(entry.get("label"))
        # A standard type's name is the form's, not a field; an added row's label is one a reviewer can edit
        label_edits = [] if entry.get("_standard") else entry_edits(redlines, entry, "label")
        prefix = None
        if needs_label:
            stamp_field(workbook, sheet, f"{layout.label_column}{row}", entry.get("label"), label_edits)
        elif label and label.lower() not in layout.row_names:
            # A variant on a shared pre-printed row (Roller - Dynamic on Gen Bk's Roller) keeps its name by the model
            prefix = label
        model_column, number_column = layout.slots[slot]
        stamp_field(workbook, sheet, f"{model_column}{row}", entry.get("model"),
                    entry_edits(redlines, entry, "model"), prefix=prefix)
        stamp_field(workbook, sheet, f"{number_column}{row}", entry.get("number"),
                    entry_edits(redlines, entry, "number"), count_value)
        wrote = True
    if missing:
        workbook.set_cell(sheet, f"{layout.label_column}{layout.free_rows[-1]}", f"+{missing + 1} more (see ICID)")
    return wrote


def checklist_answer(value: Any) -> Optional[str]:
    """
    Normalise a checklist answer (safety checklist, A/C requirements), including older reports' booleans.
    Takes the saved value.
    Returns 'Y', 'N', 'NA', or None when unanswered.
    """
    if value is True:
        return "Y"
    if value is False:
        return "N"
    return value if value in ("Y", "N", "NA") else None


def tick_box(workbook: WorkbookTemplate, sheet: str, cell: str, ticked: bool) -> None:
    """
    Tick (or clear) one drawn checkbox rectangle: a small centred "X" in the cell under it.
    Takes the workbook, sheet name, the cell under the rectangle and whether to tick it.
    Returns nothing.
    """
    workbook.set_cell(sheet, cell, CHECK_MARK if ticked else None)
    if ticked:
        workbook.set_font_size(sheet, cell, BOX_MARK_FONT_PT)
        workbook.center_cell(sheet, cell)


def stamp_safety(workbook: WorkbookTemplate, sheet: str, layout: SafetyLayout, data: dict[str, Any],
                 redlines: Redlines = NO_REDLINES) -> bool:
    """
    Write the MPT/safety checklist: an X under Y or N, and the remarks. The forms have no N/A column, so an N/A answer
    leaves both boxes empty and is written at the start of the remarks instead. An answer or a remark a reviewer
    edited prints with its marks (see stamp_checklist).
    Takes the workbook, sheet name, the checklist's layout, the report_data and the report's redlines (none unless
    given).
    Returns whether any answer or remark was written.
    """
    checks, remarks = section(data, "safetyChecks"), section(data, "safetyRemarks")
    edits = {key: (redlines.field(f"safetyChecks.{key}"), redlines.field(f"safetyRemarks.{key}"))
             for key in layout.rows}
    return stamp_checklist(workbook, sheet, layout, {key: (checks.get(key), remarks.get(key)) for key in layout.rows},
                           edits)


def stamp_answer_box(workbook: WorkbookTemplate, sheet: str, cell: str, option: str, chain: list[RedlineEntry],
                     initials: Optional[str], blank: Optional[str] = None) -> None:
    """
    Mark one box (Y or N) of an answer a reviewer edited: an X in the redline colour when it is the reviewer's
    answer that stands, a struck X where an answer was replaced (black for the inspector's), nothing otherwise.
    Takes the workbook, sheet, the box's cell, the answer it stands for, the answer's chain, the initials to
    print after a standing X (None where the row's remarks take them) and what an unmarked box holds (nothing,
    unless the form pre-prints a letter there).
    Returns nothing.
    """
    standing = chain[-1]
    replaced = [entry for entry in chain[:-1] if entry.text == option]
    if standing.text == option:
        color = PAY_REDLINE_COLOR if standing.by_reviewer else None
        runs = [TextRun(CHECK_MARK, color=color)]
        if initials:
            runs.append(TextRun(f" {initials}", color=PAY_REDLINE_COLOR, points=REDLINE_INITIALS_FONT_PT))
            workbook.set_cell_runs(sheet, cell, runs)
            workbook.shrink_on_one_line(sheet, cell)
            return
        workbook.set_cell_runs(sheet, cell, runs)
    elif replaced:
        color = PAY_REDLINE_COLOR if replaced[-1].by_reviewer else None
        workbook.set_cell_runs(sheet, cell, [TextRun(CHECK_MARK, color=color, strike=True)])
    else:
        workbook.set_cell(sheet, cell, blank)


def stamp_checklist(workbook: WorkbookTemplate, sheet: str, layout: SafetyLayout, answers: dict[str, tuple[Any, Any]],
                    edits: Optional[dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]]] = None) -> bool:
    """
    Write a Y / N checklist: an X under Y or N, and the remarks. An N/A answer has no box of its own, so it leaves both
    boxes empty and is written at the start of the remarks instead ("N/A", or "N/A — <remarks>"). On a form with no
    remarks column, remarks (and so N/A) aren't printed.
    An answer a reviewer changed keeps a struck X in the box it left and gets an X in the redline colour in its new
    one, with the initials of whoever changed it at the start of the row's remarks (after the X itself where the form
    has no remarks column). A remark a reviewer edited prints as its chain, on one line shrunk to fit.
    Takes the workbook, sheet name, the checklist's layout, each row's (answer, remarks) by key, and each row's
    (answer edits, remark edits) by key, oldest first (none unless given).
    Returns whether any answer or remark was written.
    """
    wrote = False
    for key, row in layout.rows.items():
        saved_answer, saved_remark = answers.get(key, (None, None))
        answer_edits, remark_edits = (edits or {}).get(key, ([], []))
        answer, remark = checklist_answer(saved_answer), text_value(saved_remark)
        chain = redline_chain(answer_edits, saved_answer, checklist_answer)
        initials = " ".join(dict.fromkeys(entry.initials for entry in chain if entry.initials)) or None
        for column, option in ((layout.yes_column, "Y"), (layout.no_column, "N")):
            if chain:
                stamp_answer_box(workbook, sheet, f"{column}{row}", option, chain,
                                  initials if layout.remarks_column is None else None)
            else:
                workbook.set_cell(sheet, f"{column}{row}", CHECK_MARK if answer == option else None)
        if layout.remarks_column is None:
            wrote = wrote or answer in ("Y", "N") or bool(chain)
            continue
        cell = f"{layout.remarks_column}{row}"
        if initials or remark_edits:
            runs = [TextRun(f"{initials} ", color=PAY_REDLINE_COLOR, points=REDLINE_INITIALS_FONT_PT)] if initials else []
            remark_runs = redline_runs(redline_chain(remark_edits, saved_remark)) or ([TextRun(remark)] if remark else [])
            if answer == "NA":
                runs.append(TextRun("N/A — " if remark_runs else "N/A"))
            workbook.set_cell_runs(sheet, cell, runs + remark_runs)
            workbook.shrink_on_one_line(sheet, cell)
        else:
            if answer == "NA":
                remark = f"N/A — {remark}" if remark else "N/A"
            workbook.set_cell(sheet, cell, remark)
        wrote = wrote or answer is not None or remark is not None or bool(chain) or bool(remark_edits)
    return wrote


# ---- Long text: front page -> back page -> Report Cont ----------------------
#
# A report's Description of Work fills its front page's ruled lines; what doesn't fit continues on the back page's
# remark lines under "Description of work (continued):", followed by the comments ("Comments:" when both are there).
# What doesn't fit there continues on the Report Cont sheet, and what doesn't fit on Report Cont is cut with a note
# pointing to ICID. The template has one Report Cont, so only one report per export can continue onto it; a report
# that can't has its back page's last line cut instead.

REPORT_CONT = "Report Cont"
REPORT_CONT_HEADER = ContinuationHeader(
    project_cells={"G14": "project_id", "P14": "registration_code", "I15": "project_description", "F17": "borough"},
    date="I10", day_of_week=("I11", "J11", "K11", "L11", "M11", "N11", "O11"), ir_no="U10", sheet_no="AA10",
    inspector="H19",
)

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
              report_cont: Optional[TextArea], redlines: Redlines = NO_REDLINES) -> TextFlow:
    """
    Lay a report's description and comments out across its front page, back page and (when available) Report Cont.
    One a reviewer edited is laid out as its chain: the replaced text struck, then the new text (see
    redline_paragraphs), so it takes that many more lines.
    Takes the description and comments (non-text counts as empty), the front and back areas, Report Cont's area, or
    None when another report already uses Report Cont, and the report's redlines (none unless given).
    Returns a TextFlow with each area's lines; text past the last available area is cut with the "continued" note.
    """
    queue = redline_paragraphs(description, redlines.field("description"))
    front_lines = fill_lines(queue, len(front.rows), front.line_chars)
    past_front = bool(queue)
    remarks = redline_paragraphs(comments, redlines.field("comments"))
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


def stamp_continuation_header(workbook: WorkbookTemplate, sheet: str, layout: ContinuationHeader,
                              idr: dict[str, Any], project: dict[str, Any], inspector: Optional[str]) -> None:
    """
    Write a continuation form's header: project details as values (not Contract Info formulas), the date as m/d/yy
    text, the day of the week, and the inspector; I.R. No. takes the IDR's number (cleared when it has none) and
    "Sheet No.: ____ of ____" keeps only its label (these pages aren't numbered).
    Takes the workbook, the sheet, its header layout, the IDR row, the project row and the inspector's name.
    Returns nothing.
    """
    workbook.set_cell(sheet, layout.date, short_date(idr["report_date"]))
    highlight_day(workbook, sheet, layout.day_of_week, idr["report_date"])
    workbook.set_cell(sheet, layout.ir_no, ir_number(idr))
    workbook.set_cell(sheet, layout.sheet_no, "Sheet No.:")
    for cell, field in layout.project_cells.items():
        workbook.set_cell(sheet, cell, project.get(field))
    workbook.set_cell(sheet, layout.inspector, inspector)


def stamp_report_cont(workbook: WorkbookTemplate, idr: dict[str, Any], project: dict[str, Any],
                      inspector: Optional[str], lines: list[str]) -> None:
    """
    Fill the Report Cont sheet: its header (date, day, project details as values, inspector; its blank-line
    placeholders cleared) and the continued text on its ruled lines.
    Takes the workbook, IDR row, project details, inspector name and the lines.
    Returns nothing.
    """
    stamp_continuation_header(workbook, REPORT_CONT, REPORT_CONT_HEADER, idr, project, inspector)
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


# ---- Signatures: the inspector's and the Resident Engineer's --------------------------------------------------------

@dataclass(frozen=True)
class SignatureLayout:
    """
    Where one form page takes a signature. The image is fitted to signature_cells: the signature line and the blank
    row above it (the line alone is one 17 px row, too low to read a signature in).
    The inspector's layout names the Date cell on the same line, which takes the IDR's work date. The Resident
    Engineer's has no Date cell of its own (the page has one, and it is the inspector's), so it names the caption
    under its line instead, which is replaced by "RE: <name>, <date>".
    """

    signature_cells: str                 # the range the image is fitted to, e.g. "C58:M59"
    signature_cx_emu: int                # that range's width
    signature_cy_emu: int                # and height
    date_cell: Optional[str] = None      # top-left cell of the line's Date cell (the inspector's layout)
    column_px: int = 19                  # width of each column in the range
    row_px: tuple[int, ...] = (17, 17)   # height of each row in the range, top to bottom
    column_widths_px: Optional[tuple[int, ...]] = None  # each column's width, for a range whose columns differ
    caption_cells: Optional[str] = None  # the caption under the line, e.g. "S60:AC60" (the RE's layout)
    caption_is_merged: bool = True       # False where the template leaves those cells unmerged


@dataclass(frozen=True)
class SignatureImage:
    """A signature ready to stamp: PNG bytes and their size in pixels."""

    data: bytes
    width: int
    height: int


# Report Cont's signature line is row 49 (C49:M49), its date AF49:AH49
REPORT_CONT_SIGNATURE = SignatureLayout(signature_cells="C48:M49", signature_cx_emu=209 * EMU_PER_PIXEL,
                                        signature_cy_emu=34 * EMU_PER_PIXEL, date_cell="AF49")
# The Resident Engineer's line on Report Cont is S49:AD49 (twelve columns), its caption S50:AD50
REPORT_CONT_RE_SIGNATURE = SignatureLayout(signature_cells="S48:AD49", signature_cx_emu=228 * EMU_PER_PIXEL,
                                           signature_cy_emu=34 * EMU_PER_PIXEL, caption_cells="S50:AD50")

# The image is stored once per page it is stamped on, so it is kept small: four times the 209 x 34 px box at most,
# which still prints sharply
SIGNATURE_MAX_PX = (836, 136)

# The time zone the RE's approval date is printed in
FORM_TIMEZONE = "America/New_York"


def prepare_signature(data: bytes) -> SignatureImage:
    """
    Read a signature image and shrink it for the workbook: at most SIGNATURE_MAX_PX, as PNG (transparency kept).
    Takes the file's bytes.
    Returns the SignatureImage; raises Pillow's error for a file it can't read.
    """
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGBA")
        image.thumbnail(SIGNATURE_MAX_PX)
        output = io.BytesIO()
        image.save(output, "PNG", optimize=True)
        return SignatureImage(output.getvalue(), image.width, image.height)


def signed_date(signed_at: datetime) -> date:
    """
    Work out the calendar day a signing time falls on in FORM_TIMEZONE.
    Takes the time (taken as UTC when it carries no zone).
    Returns the date; in UTC if the zone's data isn't available.
    """
    moment = signed_at if signed_at.tzinfo else signed_at.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(ZoneInfo(FORM_TIMEZONE)).date()
    except ZoneInfoNotFoundError:
        return moment.astimezone(timezone.utc).date()


def _column_letters(number: int) -> str:
    """
    Name a column by its number.
    Takes the 1-based column number.
    Returns its letters, e.g. 3 -> "C", 31 -> "AE".
    """
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _column_at(layout: SignatureLayout, first_column: int, left: int) -> tuple[int, int]:
    """
    Find the column a point falls in, counting pixels from the left edge of a layout's signature cells.
    Takes the layout, the number of its first column and the distance in pixels.
    Returns (the column's number, how far into that column the point is).
    """
    if layout.column_widths_px is None:
        return first_column + left // layout.column_px, left % layout.column_px
    column = first_column
    for width in layout.column_widths_px[:-1]:
        if left < width:
            break
        left -= width
        column += 1
    return column, left


def _stamp_signature_image(workbook: WorkbookTemplate, sheet: str, layout: SignatureLayout,
                           signature: SignatureImage, description: str) -> None:
    """
    Place a signature image on one page: as large as fits the layout's signature cells, keeping its proportions,
    centred in them.
    Takes the workbook, the sheet, its SignatureLayout, the signature and the picture's description.
    Returns nothing.
    """
    box_width, box_height = layout.signature_cx_emu // EMU_PER_PIXEL, layout.signature_cy_emu // EMU_PER_PIXEL
    scale = min(box_width / signature.width, box_height / signature.height)
    width, height = max(1, round(signature.width * scale)), max(1, round(signature.height * scale))
    left, top = (box_width - width) // 2, (box_height - height) // 2

    first = re.fullmatch(r"([A-Z]+)(\d+):[A-Z]+\d+", layout.signature_cells)
    first_column = sum((ord(letter) - ord("A") + 1) * 26 ** place
                       for place, letter in enumerate(reversed(first.group(1))))
    row = int(first.group(2))
    for row_height in layout.row_px[:-1]:  # walk down to the row the image's top edge falls in
        if top < row_height:
            break
        top -= row_height
        row += 1
    column, offset_x = _column_at(layout, first_column, left)
    workbook.add_picture(sheet, signature.data, "png", f"{_column_letters(column)}{row}", width, height,
                         offset_x_px=offset_x, offset_y_px=top, description=description)


def stamp_signature(workbook: WorkbookTemplate, sheet: str, layout: SignatureLayout,
                    signature: Optional[SignatureImage]) -> None:
    """
    Stamp the inspector's signature on one page: the image fitted to the layout's signature cells. The line's Date
    cell is stamped separately (stamp_signature_date), since it belongs to whichever signature is the latest.
    Takes the workbook, the sheet, its SignatureLayout and the signature (None leaves the page as it is).
    Returns nothing.
    """
    if signature is None:
        return
    _stamp_signature_image(workbook, sheet, layout, signature, "Inspector's signature")


def stamp_signature_date(workbook: WorkbookTemplate, sheet: str, layout: SignatureLayout,
                         signed_at: Optional[datetime]) -> None:
    """
    Stamp the signature line's Date cell, the one Date a page has, with the day a signature was made: m/d/yy, the day
    it was in FORM_TIMEZONE.
    Takes the workbook, the sheet, the inspector's SignatureLayout (it names the Date cell) and the signing time
    (None leaves the cell blank).
    Returns nothing.
    """
    if signed_at is not None:
        workbook.set_cell(sheet, layout.date_cell, short_date(signed_date(signed_at)))


def re_signature_caption(name: Optional[str]) -> str:
    """
    Word the caption under the Resident Engineer's signature.
    Takes the approver's name (None when it isn't known).
    Returns "RE: <name>", or "RE:" alone. The day they signed goes in the page's Date cell, not here.
    """
    return f"RE: {name}" if name else "RE:"


def stamp_re_signature(workbook: WorkbookTemplate, sheet: str, layout: SignatureLayout,
                       signature: Optional[SignatureImage], name: Optional[str]) -> None:
    """
    Stamp the Resident Engineer's signature on one page: the image fitted to the layout's signature cells, and
    "RE: <name>" in place of the caption under the line, shrunk to fit if it is long.
    Takes the workbook, the sheet, its RE SignatureLayout, the signature (None leaves the page as it is: no image and
    the printed caption) and the approver's name.
    Returns nothing.
    """
    if signature is None:
        return
    _stamp_signature_image(workbook, sheet, layout, signature, "Resident Engineer's signature")
    caption = layout.caption_cells.split(":")[0]
    if not layout.caption_is_merged:
        workbook.merge_cells(sheet, layout.caption_cells)  # so the text shrinks to the line's width, not one column's
    workbook.set_cell(sheet, caption, re_signature_caption(name))
    workbook.shrink_to_fit_cell(sheet, caption)
