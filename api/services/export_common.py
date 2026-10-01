"""
What every DDC report form's front page shares: the IDR header block (project details, date and day, I.R. No.,
sheet number, times, temperatures, weather and inspector), and the value formatting used to fill it.

Each form puts that block in nearly the same cells; a HeaderLayout names them for one sheet, and
stamp_common_header fills them. Per-report modules (export_general, export_swcb, ...) own their sheet's layout.
"""

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Any, Optional

from api.services.xlsx_template import WorkbookTemplate


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
