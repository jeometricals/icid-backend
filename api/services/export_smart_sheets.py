"""
The SMART sheets an IDR's imported sites bring into its export (Slice X.4): one sheet of tables per site, printed
before the site's PIR pages or inspection forms (export_site_sheets), unnumbered and unsigned as those are.

A sidewalk's sheet (SMART_PIR_<address>) is what the site's page shows for the IDR: a header (the site, the inspector,
the IDR's number and work date), the flags the IDR counts there (the sketch workspace's flag table and the amount each
comes to), its curb segments (the Curb Segments table) and the pay items for the site (what its flags and segments come
to, an override in their place, the pay items entered by hand, and totals). Live before approval, the flags and
segments the IDR claimed after. A ped ramp corner's sheet (SMART_INSP_<corner>) is its inspection: status and signers,
the corner's fields, its ramps' measurements with their ADA warnings, and the pay items entered on its ramps' sites.

Each sheet is a sheet of its own (WorkbookTemplate.add_sheet), printed on US Letter one page wide, landscape when its
columns need it, with a page break before a section that wouldn't start on the page it would land on. Everything here
is best-effort: a site whose data can't be read is logged and gets no sheet; the export goes on without it.
"""

import logging
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Optional
from uuid import UUID

from api.queries.idr_flags import list_idr_flags
from api.queries.idr_report_site_pay_items import idr_site_pay_items
from api.queries.idr_segments import list_idr_segments
from api.queries.ped_ramp_corners import get_corner
from api.queries.ped_ramp_inspections import get_corner_inspection
from api.schemas.ped_ramp import CHOICE_LABELS, COMMON_FIELDS, CORNER_LABELS, FIELD_LABELS, TYPE_FIELDS
from api.schemas.property import display_address_of
from api.services.export_common import ir_number, short_date, signed_date
from api.services.idr_flags import is_frozen
from api.services.ped_ramp_report import inspection_report, shown
from api.services.quantities import canonical_unit
from api.services.xlsx_template import CellValue, WorkbookTemplate

logger = logging.getLogger(__name__)

PIR_PREFIX = "SMART_PIR_"
INSPECTION_PREFIX = "SMART_INSP_"
MAX_SHEET_NAME = 31  # Excel's limit on a sheet's name
CENT = Decimal("0.01")

# A sidewalk's sheet: eight columns, the flag table's (its widest) one each; landscape
PIR_WIDTHS = {"A": 7, "B": 9, "C": 16, "D": 17, "E": 36, "F": 15, "G": 13, "H": 17}
# A corner's: a label and a value twice over, or a field and up to three ramps; portrait, as wide as portrait holds so
# its longest labels don't wrap
INSPECTION_WIDTHS = {"A": 33, "B": 22, "C": 28, "D": 22}
# The corner's fields a corner's sheet prints, two to a row: the rest of the DOT form's sections 1-3 are on its DOT
# inspection form, and its streets, borough and direction in the sheet's title
CORNER_FIELDS = ("contractor_name", "construction_completion_date", "curb_material", "sidewalk_material")
# Wider than this many characters prints landscape; narrower, portrait (Letter's 7.5" across at fit width)
PORTRAIT_MAX_CHARS = 105
# How much a Letter page holds down, in points, inside 0.5" margins: 10" portrait, 7.5" landscape
PAGE_POINTS = {False: 720.0, True: 540.0}
# What a page holds across, in characters of the default font at 100%: 7.5" portrait, 10" landscape
PAGE_CHARS = {False: 105.0, True: 140.0}
ROW_POINTS = 12.75  # one line of Arial 10
TITLE_POINTS = 18.75
# A section starts on a fresh page unless its title, its headings and this many rows fit on the one it would land on
KEEP_WITH_HEADING = 3

HEADING_FILL = "FFD9D9D9"
GREY = "FF808080"
RED = "FFC00000"
DASH = "—"

SHAPE_LABELS = {"rectangle": "Rectangle", "trapezoid": "Trapezoid", "triangle": "Right triangle",
                "polygon": "Polygon", "arc": "Arc"}
THICKNESS_LABELS = {"4in": '4"', "7in_as_4in": '7" as 4"', "7in": '7"'}
SEGMENT_TYPE_LABELS = {"curb_concrete": "Concrete curb", "curb_concrete_depressed": "Depressed concrete curb",
                       "curb_steel_faced": "Steel-faced curb", "curb_steel_faced_corner": "Steel-faced corner curb",
                       "curb_granite": "Granite curb", "other": "Other"}
OVERRIDDEN_NOTE = ("* Its pay item is overridden on this site for this IDR: the override in the pay items below "
                   "bills in place of what these come to.")


@dataclass(frozen=True)
class Column:
    """One column of a table: its heading, the sheet columns it spans (merged when more than one) and its value."""

    heading: str
    first: str
    last: str
    value: Callable[[dict[str, Any]], CellValue]


def _sf(value: Any) -> str:
    """
    Write an area as the flag table does: "1,234.50 SF".
    Takes the area in square feet.
    Returns the text.
    """
    return f"{Decimal(str(value)):,.2f} SF"


def _amount(value: Any, unit: Optional[str]) -> str:
    """
    Write a quantity with its unit, to 2 decimal places: "180.00 SF".
    Takes the quantity and the unit (None for none).
    Returns the text.
    """
    return f"{Decimal(str(value)):,.2f} {unit or ''}".strip()


def _pay_item(row: dict[str, Any]) -> str:
    """
    Name a drawing's pay item as the sketch workspace's tables do: "item no — description", or a dash for none.
    Takes a flag or segment row (pay_item_id, pay_item_ref, pay_item_description).
    Returns the text.
    """
    if row.get("pay_item_id") is None:
        return DASH
    if row.get("pay_item_ref") is None:
        return "Not in the catalog"
    return f"{row['pay_item_ref']} {DASH} {row.get('pay_item_description') or ''}".strip()


def _derived(row: dict[str, Any]) -> str:
    """
    Write what a drawing comes to in its pay item's unit, starred when its site overrides that pay item.
    Takes a flag or segment row (amount, unit, overridden).
    Returns the text, or a dash when it comes to nothing.
    """
    if row.get("amount") is None:
        return DASH
    return _amount(row["amount"], row.get("unit")) + (" *" if row.get("overridden") else "")


def _dimensions(segment: dict[str, Any]) -> str:
    """
    Write a segment's dimensions as the Curb Segments table does: "34.25 ft · 3 runs", "no scale" without a length.
    Takes the segment row (length_ft, point_count).
    Returns the text.
    """
    runs = max((segment.get("point_count") or 0) - 1, 0)
    length = "no scale" if segment.get("length_ft") is None else f"{Decimal(str(segment['length_ft'])):.2f} ft"
    return f"{length} · {runs} {'run' if runs == 1 else 'runs'}"


# The sketch workspace's flag table (FlagsFallbackList), and what each flag comes to in its pay item's unit
FLAG_COLUMNS = (
    Column("Page", "A", "A", lambda f: f.get("page_number") or DASH),
    Column("#", "B", "B", lambda f: f.get("display_order")),
    Column("Shape", "C", "C", lambda f: SHAPE_LABELS.get(f.get("shape"), f.get("shape"))),
    Column("Thickness", "D", "D", lambda f: THICKNESS_LABELS.get(f.get("surveyed_thickness"), f.get("surveyed_thickness"))),
    Column("Pay item", "E", "E", _pay_item),
    Column("Area (computed)", "F", "F", lambda f: DASH if f.get("computed_quantity") is None else _sf(f["computed_quantity"])),
    Column("Reported", "G", "G", lambda f: DASH if f.get("reported_quantity") is None else _sf(f["reported_quantity"])),
    Column("Derived quantity", "H", "H", _derived),
)
# The Curb Segments table (SegmentsTable); the segment's number is added as the rows are laid out
SEGMENT_COLUMNS = (
    Column("Page", "A", "A", lambda s: s.get("page_number") or DASH),
    Column("Segment #", "B", "B", lambda s: s.get("number")),
    Column("Shape", "C", "C", lambda s: SEGMENT_TYPE_LABELS.get(s.get("segment_type"), s.get("segment_type"))),
    Column("Dimensions", "D", "D", _dimensions),
    Column("Pay item", "E", "E", _pay_item),
    Column("Reported", "F", "F",
           lambda s: DASH if s.get("reported_length_ft") is None else _amount(s["reported_length_ft"], "LF")),
    Column("Derived quantity", "G", "H", _derived),
)
# The site's pay items (the site page's "Pay items for this site")
PAY_ITEM_COLUMNS = (
    Column("Item", "A", "B", lambda r: r["item_no"]),
    Column("Budget code", "C", "C", lambda r: r["budget_code"]),
    Column("Description", "D", "E", lambda r: r["description"]),
    Column("Quantity", "F", "G", lambda r: r["quantity"]),
    Column("Source", "H", "H", lambda r: r["source"]),
)
# A corner's pay items, on its narrower sheet
CORNER_PAY_ITEM_COLUMNS = (
    Column("Item", "A", "A", lambda r: f"{r['item_no']} ({r['budget_code']})" if r["budget_code"] else r["item_no"]),
    Column("Description", "B", "C", lambda r: r["description"]),
    Column("Quantity", "D", "D", lambda r: r["quantity"]),
)


def sheet_name(prefix: str, label: str, taken: set[str]) -> str:
    """
    Name a SMART sheet: the prefix and the label in capitals, anything but letters and digits as one underscore, cut
    to Excel's 31 characters, and "_2", "_3", ... added (the label cut to make room) when the name is taken.
    Takes the prefix, the label (an address, a corner) and the names already used, any case.
    Returns the name, e.g. "SMART_PIR_152_23_ST".
    """
    stem = prefix + (re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper() or "SITE")
    name, number = stem[:MAX_SHEET_NAME], 1
    while name.lower() in {t.lower() for t in taken}:
        number += 1
        suffix = f"_{number}"
        name = stem[: MAX_SHEET_NAME - len(suffix)] + suffix
    return name


def is_smart_sheet(name: str) -> bool:
    """
    Tell a SMART sheet by its name, so the export leaves its printing as it was set (as many pages tall as it takes).
    Takes the sheet name.
    Returns True for a SMART sheet.
    """
    return name.startswith((PIR_PREFIX, INSPECTION_PREFIX))


def _lines(value: CellValue, width: float) -> int:
    """
    Guess how many lines a value wraps to in a cell.
    Takes the value and the cell's width in characters.
    Returns at least 1.
    """
    text = "" if value is None else str(value)
    return max(1, sum(math.ceil(len(part) / max(width - 1, 1)) or 1 for part in text.split("\n")))


class _SheetWriter:
    """Lays a SMART sheet out a row at a time: titles, label-and-value lines, tables and notes, and its page breaks."""

    def __init__(self, workbook: WorkbookTemplate, name: str, widths: dict[str, float], one_page: bool = False) -> None:
        """
        Add the sheet and ready its styles.
        Takes the workbook, the sheet's name, its column widths and whether it prints on one page (shrunk to fit, no
        page breaks).
        Returns nothing.
        """
        self.workbook, self.name, self.widths, self.one_page = workbook, name, widths, one_page
        self.last_column = max(widths, key=lambda letter: (len(letter), letter))
        self.landscape = sum(widths.values()) > PORTRAIT_MAX_CHARS
        # Fit to one page wide shrinks the sheet when it is wider than the page, and so lets more rows on a page
        scale = min(1.0, PAGE_CHARS[self.landscape] / sum(widths.values()))
        self.page_points = PAGE_POINTS[self.landscape] / scale
        self.row = 2  # row 1 is left for a draft's marker
        self.used = 0.0  # points used on the page being filled
        self.breaks: list[int] = []
        workbook.add_sheet(name, widths)
        top = workbook.top_aligned_style(workbook.wrap_text_style(None))
        self.styles = {
            "title": workbook.font_style(None, points=14, bold=True),
            "section": workbook.font_style(None, points=12, bold=True),
            "label": workbook.top_aligned_style(workbook.font_style(None, bold=True)),
            "value": top,
            "heading": workbook.table_style(workbook.font_style(top, bold=True), HEADING_FILL),
            "cell": workbook.table_style(top),
            "warned": workbook.table_style(workbook.font_style(top, bold=True, rgb=RED)),
            "total": workbook.table_style(workbook.font_style(top, bold=True)),
            "note": workbook.font_style(top, points=9, rgb=GREY),
            "warning": workbook.font_style(top, points=9, rgb=RED),
        }

    def _span_width(self, first: str, last: str) -> float:
        """
        Add up the widths of a run of columns.
        Takes its first and last column letters.
        Returns the width in characters.
        """
        letters = sorted(self.widths, key=lambda letter: (len(letter), letter))
        return sum(self.widths[letter] for letter in letters[letters.index(first): letters.index(last) + 1])

    def _cells(self, first: str, last: str) -> list[str]:
        """
        List a run of cells on the current row.
        Takes its first and last column letters.
        Returns their references.
        """
        letters = sorted(self.widths, key=lambda letter: (len(letter), letter))
        return [f"{letter}{self.row}" for letter in letters[letters.index(first): letters.index(last) + 1]]

    def _put(self, first: str, last: str, value: CellValue, style: str) -> int:
        """
        Write a value into a run of cells on the current row (merged when more than one), each cell styled.
        Takes the run's first and last column letters, the value and the style.
        Returns how many lines it wraps to.
        """
        cells = self._cells(first, last)
        self.workbook.set_cell(self.name, cells[0], value, style)
        for cell in cells[1:]:
            self.workbook.set_cell(self.name, cell, None, style)
        if len(cells) > 1:
            self.workbook.merge_cells(self.name, f"{cells[0]}:{cells[-1]}")
        return _lines(value, self._span_width(first, last))

    def _end_row(self, lines: int, points: float = ROW_POINTS) -> None:
        """
        Finish the current row: its height for the lines it holds, and the page it is on.
        Takes how many lines it holds and a line's height.
        Returns nothing.
        """
        height = points * lines
        if lines > 1 or points != ROW_POINTS:
            self.workbook.set_row_height(self.name, self.row, height)
        if self.used + height > self.page_points:
            self.used = 0.0
        self.used += height
        self.row += 1

    def _keep_together(self, points: float) -> None:
        """
        Break the page before what comes next unless that much of it fits on the page being filled.
        Takes the height it needs, in points.
        Returns nothing.
        """
        if self.used and self.used + points > self.page_points:
            self.breaks.append(self.row - 1)
            self.used = 0.0

    def title(self, text: str) -> None:
        """
        Write the sheet's title line.
        Takes the text.
        Returns nothing.
        """
        self._put("A", self.last_column, text, self.styles["title"])
        self._end_row(1, TITLE_POINTS)

    def pairs(self, pairs: list[tuple[str, CellValue]], spans: tuple[tuple[str, str, str, str], ...]) -> None:
        """
        Write label-and-value pairs, as many to a row as spans are given.
        Takes the pairs and each pair's place on a row: (label's first, last, value's first, last column).
        Returns nothing.
        """
        for start in range(0, len(pairs), len(spans)):
            lines = 1
            for (label, value), (lf, ll, vf, vl) in zip(pairs[start: start + len(spans)], spans):
                lines = max(lines, self._put(lf, ll, label, self.styles["label"]),
                            self._put(vf, vl, "" if value is None else value, self.styles["value"]))
            self._end_row(lines)

    def blank(self) -> None:
        """
        Leave a blank row.
        Takes nothing.
        Returns nothing.
        """
        self._end_row(1)

    def section(self, title: str, rows_after: int = KEEP_WITH_HEADING) -> None:
        """
        Start a section: on a fresh page when its title and its first rows wouldn't fit, then its title.
        Takes the title and how many of its rows must fit with it.
        Returns nothing.
        """
        self.blank()
        self._keep_together(TITLE_POINTS + ROW_POINTS * (1 + rows_after))
        self._put("A", self.last_column, title, self.styles["section"])
        self._end_row(1, TITLE_POINTS)

    def table(self, columns: tuple[Column, ...], rows: list[dict[str, Any]], warned: Callable[[dict, Column], bool]
              = lambda row, column: False) -> None:
        """
        Write a table: its headings, then a row each.
        Takes the columns, the rows and which cells to print as warnings (red).
        Returns nothing.
        """
        lines = max(self._put(c.first, c.last, c.heading, self.styles["heading"]) for c in columns)
        self._end_row(lines)
        for row in rows:
            lines = max(self._put(c.first, c.last, c.value(row),
                                  self.styles["warned" if warned(row, c) else "total" if row.get("_total") else "cell"])
                        for c in columns)
            self._end_row(lines)

    def note(self, text: str, style: str = "note") -> None:
        """
        Write a note across the sheet.
        Takes the text and its style ("note", or "warning" for red).
        Returns nothing.
        """
        self._end_row(self._put("A", self.last_column, text, self.styles[style]))

    def finish(self) -> None:
        """
        Set how the sheet prints: Letter, its orientation, a page wide, its print area and its page breaks (or one page,
        without them).
        Takes nothing (a draft's marker is stamped by the export, as on every page).
        Returns nothing.
        """
        self.workbook.set_print_layout(self.name, self.landscape, f"A1:{self.last_column}{max(self.row - 1, 1)}",
                                       [] if self.one_page else self.breaks, self.one_page)


def _counted(row: dict[str, Any]) -> bool:
    """
    Whether a flag or segment counts for the IDR: attributed to it before approval, claimed by it after.
    Takes the row.
    Returns True when it counts.
    """
    return row["state"] in ("attributed", "claimed")


def _sources(flags: int, segments: int) -> str:
    """
    Word what an automatic quantity comes from: "auto (8 flags)", "auto (1 flag, 2 curb segments)".
    Takes how many flags and segments.
    Returns the words.
    """
    parts = [f"{flags} flag{'' if flags == 1 else 's'}"] if flags else []
    parts += [f"{segments} curb segment{'' if segments == 1 else 's'}"] if segments else []
    return f"auto ({', '.join(parts)})"


def site_pay_item_rows(flags: list[dict[str, Any]], segments: list[dict[str, Any]],
                       entered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Lay out a site's pay items for the IDR as its page's table does: a row per pay item its counting flags and segments
    fill (their amount, or the site's override in its place, marked edited with the amount it replaced), then each pay
    item entered by hand, then any override whose flags and segments are gone; an override an approval dropped is
    listed as billing nothing. Then a total per unit of what bills.
    Takes the site's flag and segment rows and its site pay items (idr_site_pay_items).
    Returns rows of {item_no, budget_code, description, quantity (text), source}, the totals with _total set.
    """
    autos: dict[UUID, dict[str, Any]] = {}
    for drawing, is_flag in [(f, True) for f in flags] + [(s, False) for s in segments]:
        if not _counted(drawing) or drawing.get("amount") is None:
            continue
        auto = autos.setdefault(drawing["pay_item_id"], {
            "item_no": drawing.get("pay_item_ref"), "budget_code": drawing.get("budget_code"),
            "description": drawing.get("pay_item_description"), "unit": canonical_unit(drawing.get("unit")) or "",
            "amount": Decimal(0), "flags": 0, "segments": 0})
        auto["amount"] += Decimal(str(drawing["amount"]))
        auto["flags" if is_flag else "segments"] += 1
    overrides = {item["contract_item_id"]: item for item in entered if item.get("overrides_auto")}

    rows: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = {}

    def add(item_no: Any, budget_code: Any, description: Any, quantity: Decimal, unit: str, source: str,
            bills: bool) -> None:
        rows.append({"item_no": item_no or DASH, "budget_code": budget_code or "", "description": description or "",
                     "quantity": _amount(quantity, unit), "source": source})
        if bills:
            totals[unit] = totals.get(unit, Decimal(0)) + quantity

    for contract_item_id, auto in autos.items():
        amount = auto["amount"].quantize(CENT, rounding=ROUND_HALF_UP)
        source = _sources(auto["flags"], auto["segments"])
        override = overrides.pop(contract_item_id, None)
        if override is None:
            add(auto["item_no"], auto["budget_code"], auto["description"], amount, auto["unit"], source, True)
        elif override.get("override_dropped"):
            add(auto["item_no"], auto["budget_code"], auto["description"], Decimal(str(override["quantity"])),
                auto["unit"], "override dropped at approval (bills nothing)", False)
        else:
            add(auto["item_no"], auto["budget_code"], auto["description"], Decimal(str(override["quantity"])),
                auto["unit"], f"{source} · edited, originally {_amount(amount, auto['unit'])}", True)
    for item in entered:
        unit = canonical_unit(item.get("unit")) or ""
        quantity = Decimal(str(item["quantity"]))
        if not item.get("overrides_auto"):
            note = f": {item['note']}" if item.get("note") else ""
            add(item.get("pay_item_ref"), item.get("budget_code"), item.get("description"), quantity, unit,
                f"entered{note}", True)
        elif item["contract_item_id"] in overrides:
            dropped = item.get("override_dropped")
            add(item.get("pay_item_ref"), item.get("budget_code"), item.get("description"), quantity, unit,
                "override dropped at approval (bills nothing)" if dropped else "override (no flags left)", not dropped)
    for unit, total in sorted(totals.items()):
        rows.append({"item_no": "Total", "budget_code": "", "description": "", "quantity": _amount(total, unit),
                     "source": "", "_total": True})
    return rows


class SmartSheets:
    """
    Builds an IDR's SMART sheets as export_site_sheets meets its sites: called with a site row (idr_site_sheets), it
    adds the site's sheet to the workbook and returns its name. The IDR's flags, segments and site pay items are read
    once, at the first site.
    """

    def __init__(self, workbook: WorkbookTemplate, idr: dict[str, Any], inspector: Optional[str]) -> None:
        """
        Ready the builder.
        Takes the workbook, the IDR row and the inspector's name.
        Returns nothing.
        """
        self.workbook, self.idr, self.inspector = workbook, idr, inspector
        self.taken: set[str] = set(workbook.sheet_names())
        self._data: Optional[tuple[list, list, list]] = None

    def _load(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Read the IDR's flags and segments (live, or those it claimed once approved) and its site pay items, once.
        Takes nothing.
        Returns (flags, segments, site pay items); raises RuntimeError when any can't be read.
        """
        if self._data is None:
            frozen = is_frozen(self.idr)
            flags = list_idr_flags(self.idr["idr_id"], frozen)
            segments = list_idr_segments(self.idr["idr_id"], frozen)
            entered = idr_site_pay_items(self.idr["idr_id"])
            if flags is None or segments is None or entered is None:
                raise RuntimeError("the IDR's flags, curb segments or site pay items couldn't be read")
            self._data = (flags, segments, entered)
        return self._data

    def __call__(self, site: dict[str, Any]) -> list[str]:
        """
        Add a site's SMART sheet, best-effort.
        Takes the site row (idr_site_sheets): a sidewalk, or a ped ramp whose corner prints here.
        Returns the sheet's name in a list; empty when it couldn't be made (logged).
        """
        try:
            if site["kind"] == "sidewalk":
                return [self._sidewalk(site)]
            return [self._corner(site)]
        except Exception as exc:  # noqa: BLE001 - one site's sheet mustn't stop the export
            logger.warning("SMART sheet of site %s couldn't be made for the export: %s", site.get("id"), exc)
            return []

    def _name(self, prefix: str, label: str) -> str:
        """
        Name a new sheet and keep the name taken.
        Takes the prefix and the label.
        Returns the name.
        """
        name = sheet_name(prefix, label, self.taken)
        self.taken.add(name)
        return name

    def _header(self, sheet: _SheetWriter, title: str, place: Optional[str]) -> None:
        """
        Write a sheet's header: its title, where the site is, and the inspector, the IDR's number and its work date.
        Takes the sheet, its title and the site's place (borough, block and lot; None for none).
        Returns nothing.
        """
        sheet.title(title)
        if place:
            sheet.note(place, "note")
        work_date = self.idr.get("report_date")
        sheet.pairs([("Inspector", self.inspector), ("IDR No.", ir_number(self.idr) or "(not yet numbered)"),
                     ("Work date", short_date(work_date) if work_date else None),
                     ("Status", str(self.idr.get("status", "")).replace("_", " ").capitalize())],
                    self._header_spans(sheet))

    @staticmethod
    def _header_spans(sheet: _SheetWriter) -> tuple[tuple[str, str, str, str], ...]:
        """
        Place the header's label-and-value pairs: two to a row.
        Takes the sheet.
        Returns the spans, as pairs() takes them.
        """
        if sheet.last_column == "H":
            return (("A", "B", "C", "D"), ("E", "E", "F", "H"))
        return (("A", "A", "B", "B"), ("C", "C", "D", "D"))

    def _sidewalk(self, site: dict[str, Any]) -> str:
        """
        Add a sidewalk's sheet: header, flags, curb segments and pay items.
        Takes the site row.
        Returns the sheet's name.
        """
        flags, segments, entered = self._load()
        address = display_address_of(site["kind"], site.get("address"), site.get("intersection_street_1"),
                                     site.get("intersection_street_2"), site.get("quadrant"), site.get("facing_street"))
        site_flags = [f for f in flags if f["property_id"] == site["property_id"]]
        site_segments = [s for s in segments if s["property_id"] == site["property_id"]]
        site_entered = [item for item in entered if item["report_site_id"] == site["id"]]
        place = f"{site['boro']} Block {site['block']} Lot {site['lot']}" if site.get("block") else None

        sheet = _SheetWriter(self.workbook, self._name(PIR_PREFIX, site.get("address") or address), PIR_WIDTHS)
        self._header(sheet, f"SMART PIR {DASH} {address}", place)

        counted = [f for f in site_flags if _counted(f)]
        sheet.section("Flags")
        if counted:
            sheet.table(FLAG_COLUMNS, counted)
        else:
            sheet.note("No flags on this site count for this IDR.")
        others = len(site_flags) - len(counted)
        if others:
            sheet.note(f"{others} more flag{'' if others == 1 else 's'} on this site "
                       f"{'is' if others == 1 else 'are'} not this IDR's (excluded, or claimed by another IDR).")
        if any(f.get("overridden") for f in counted):
            sheet.note(OVERRIDDEN_NOTE)

        counted_segments = [{**s, "number": n} for n, s in enumerate((s for s in site_segments if _counted(s)), 1)]
        sheet.section("Curb segments")
        if counted_segments:
            sheet.table(SEGMENT_COLUMNS, counted_segments)
        else:
            sheet.note("No curb segments on this site count for this IDR.")
        if any(s.get("overridden") for s in counted_segments):
            sheet.note(OVERRIDDEN_NOTE)

        self._pay_items(sheet, site_pay_item_rows(site_flags, site_segments, site_entered), PAY_ITEM_COLUMNS)
        sheet.finish()
        return sheet.name

    def _pay_items(self, sheet: _SheetWriter, rows: list[dict[str, Any]], columns: tuple[Column, ...]) -> None:
        """
        Write the "Pay items for this site" section.
        Takes the sheet, its rows (site_pay_item_rows) and the columns to lay them out in.
        Returns nothing.
        """
        sheet.section("Pay items for this site")
        if rows:
            sheet.table(columns, rows)
        else:
            sheet.note("No pay items on this site for this IDR.")

    def _corner(self, site: dict[str, Any]) -> str:
        """
        Add a ped ramp corner's sheet: header, its inspection, the corner's CORNER_FIELDS, its ramps' measurements and
        ADA warnings, and the pay items entered on its ramps' sites.
        Takes the site row (a ped ramp, its corner_id set).
        Returns the sheet's name; raises RuntimeError when the corner can't be read.
        """
        corner = get_corner(site["corner_id"])
        report = inspection_report(corner) if corner is not None else None
        if report is None:
            raise RuntimeError(f"corner {site['corner_id']} couldn't be read")
        _, _, entered = self._load()
        label = f"{site['north_south_street']} & {site['east_west_street']} {site.get('corner_direction') or ''}".strip()

        # One page, shrunk to fit: a corner of mixed ramp types lists some 46 measurements
        sheet = _SheetWriter(self.workbook, self._name(INSPECTION_PREFIX, label), INSPECTION_WIDTHS, one_page=True)
        self._header(sheet, f"SMART inspection {DASH} {label}", site.get("boro"))
        spans = self._header_spans(sheet)

        inspection = get_corner_inspection(site["corner_id"])
        sheet.section("Inspection")
        if inspection is None:
            sheet.note("No inspection of this corner yet.")
        else:
            approved = inspection.get("re_signed_at")
            sheet.pairs([("Status", str(inspection["status"]).capitalize()),
                         ("Inspector", inspection.get("inspector_name")), ("RE", inspection.get("re_name")),
                         ("Approved", short_date(signed_date(approved)) if approved else None)], spans)

        fields = report.corner.model_dump()
        sheet.section("Construction")
        sheet.pairs([(CORNER_LABELS[name], shown(name, fields.get(name))) for name in CORNER_FIELDS], spans)

        ramps = report.ramps
        sheet.section(f"Ramps at this corner ({len(ramps)})", rows_after=len(ramps) + 1)
        if not ramps:
            sheet.note("No ramps are placed at this corner yet.")
        else:
            headings = ["Field"] + [f"Ramp {n}: {CHOICE_LABELS.get(r.ramp_type, r.ramp_type)}"
                                    + (f" ({r.ramp_id_label})" if r.ramp_id_label else "") for n, r in enumerate(ramps, 1)]
            letters = ["A", "B", "C", "D"][: len(ramps) + 1]
            names = list(dict.fromkeys([f for f in COMMON_FIELDS if f != "comments"]
                                       + [f for r in ramps for f in TYPE_FIELDS[r.ramp_type]]))
            values = [r.attributes.model_dump() if r.attributes else {} for r in ramps]
            warned = [{w.field for w in r.attributes.warnings} if r.attributes else set() for r in ramps]
            columns = tuple(Column(heading, letter, letter, (lambda i: lambda row: row["cells"][i])(i))
                            for i, (heading, letter) in enumerate(zip(headings, letters)))
            rows = [{"cells": [FIELD_LABELS.get(name, name)] + [
                        shown(name, value.get(name)) + ("  (ADA)" if name in bad and value.get(name) is not None else "")
                        for value, bad in zip(values, warned)], "field": name} for name in names]
            sheet.table(columns, rows, lambda row, column: column.first != "A"
                        and row["field"] in warned[letters.index(column.first) - 1])
            sheet.section("ADA advisories")
            listed = [(n, w.message) for n, r in enumerate(ramps, 1) for w in (r.attributes.warnings if r.attributes else [])]
            for n, message in listed:
                sheet.note(f"Ramp {n}: {message}" if len(ramps) > 1 else message, "warning")
            if not listed:
                sheet.note("Every value entered is within the advisories.")

        ramp_ids = {r.property_id for r in ramps}
        corner_entered = [item for item in entered if item["property_id"] in ramp_ids]
        self._pay_items(sheet, site_pay_item_rows([], [], corner_entered), CORNER_PAY_ITEM_COLUMNS)
        sheet.finish()
        return sheet.name
