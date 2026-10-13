"""
Importing a project's pay items from a CSV: reading the file, checking every row as the admin's form would, and
sorting the rows into new items, overwrites of items the project already has, and errors.

Nothing here writes: the route hands the rows that passed to bulk_upsert_pay_items.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from api.queries.contract_items import list_project_pay_items
from api.services.pay_items import PayItemValueError, clean_pay_item_fields

# The header names, matched without regard to case; the columns may come in any order
REQUIRED_COLUMNS = ("pay_item_ref", "description", "quantity", "unit", "unit_price", "budget_code")
OPTIONAL_COLUMNS = ("spec_section",)
AMOUNT_COLUMNS = ("quantity", "unit_price")

# An amount with commas between its thousands, as a spreadsheet exports one
_WITH_THOUSANDS = re.compile(r"\d{1,3}(,\d{3})+(\.\d*)?")


class CsvFormatError(ValueError):
    """A file that can't be read as a pay-item CSV at all. Its message says why."""


@dataclass
class ParsedRow:
    """One row of the file: its number (the header is row 1), its cells as typed, and its checked values or why it failed."""

    row: int
    raw: dict[str, str]
    fields: Optional[dict[str, Any]] = None
    reason: Optional[str] = None


@dataclass
class ParseResult:
    """A file read: its rows in file order, the columns that were ignored, and whether it has a spec_section column."""

    rows: list[ParsedRow]
    ignored_columns: list[str]
    has_spec_section: bool


@dataclass
class ClassifyResult:
    """A file's rows sorted: new items, overwrites and errors as the preview shows them, and the checked values to write."""

    parsed_rows: int
    ignored_columns: list[str]
    has_spec_section: bool
    new: list[dict[str, Any]] = field(default_factory=list)
    overwrites: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    to_write: list[dict[str, Any]] = field(default_factory=list)


def _amount(text: str, name: str) -> Decimal:
    """
    Read a quantity or a price off a cell. A leading "$" and commas between thousands are dropped; nothing else is guessed.
    Takes the cell's text, trimmed, and the column's name (for the error).
    Returns the Decimal; raises PayItemValueError for a blank cell or text that isn't a number.
    """
    if not text:
        raise PayItemValueError(f"{name} cannot be blank")
    number = text.removeprefix("$").strip()
    if _WITH_THOUSANDS.fullmatch(number):
        number = number.replace(",", "")
    try:
        return Decimal(number)
    except InvalidOperation as exc:
        raise PayItemValueError(f"{name} must be a number: '{text}'") from exc


def _check_row(raw: dict[str, str], has_spec_section: bool) -> dict[str, Any]:
    """
    Check one row as the admin's form checks a pay item.
    Takes the row's cells by column name and whether the file has a spec_section column.
    Returns the values keyed by icid.contract_items column; raises PayItemValueError for the first that doesn't fit.
    """
    values: dict[str, Any] = {name: raw[name] for name in REQUIRED_COLUMNS if name not in AMOUNT_COLUMNS}
    for name in AMOUNT_COLUMNS:
        values[name] = _amount(raw[name], name)
    if has_spec_section:
        values["spec_section"] = raw["spec_section"]
    return clean_pay_item_fields(values)


def _columns(header: list[str]) -> tuple[dict[str, int], list[str]]:
    """
    Find the pay-item columns in a header row.
    Takes the header's cells.
    Returns ({column name: its position}, the other columns' names as written); raises CsvFormatError for a required column that is missing, or a column that appears twice.
    """
    positions: dict[str, int] = {}
    ignored: list[str] = []
    for position, cell in enumerate(header):
        name = cell.strip().lower()
        if name in REQUIRED_COLUMNS or name in OPTIONAL_COLUMNS:
            if name in positions:
                raise CsvFormatError(f"The column {name} appears more than once")
            positions[name] = position
        elif name:
            ignored.append(cell.strip())
    missing = [name for name in REQUIRED_COLUMNS if name not in positions]
    if missing:
        raise CsvFormatError("The file is missing the required column(s): " + ", ".join(missing))
    return positions, ignored


def _mark_duplicates(rows: list[ParsedRow]) -> None:
    """
    Turn the rows that share a ref and budget code with another row of the file into errors, each naming the others.
    Takes the file's rows; only those that passed their own checks are compared.
    Returns nothing; the rows are changed in place.
    """
    by_key: dict[tuple[str, str], list[ParsedRow]] = {}
    for row in rows:
        if row.fields is not None:
            by_key.setdefault((row.fields["item_no"], row.fields["budget_code"]), []).append(row)
    for same in by_key.values():
        if len(same) > 1:
            for row in same:
                others = [str(other.row) for other in same if other is not row]
                row.fields = None
                row.reason = "duplicated in CSV, see also row" + ("s " if len(others) > 1 else " ") + ", ".join(others)


def parse_csv(file_bytes: bytes) -> ParseResult:
    """
    Read a pay-item CSV and check every row. Rows keep the order and the numbers they have in the file (the header is row 1); a row with nothing in it is skipped.
    Takes the file's bytes: UTF-8 text (a byte-order mark is fine), comma-separated, with a header row.
    Returns a ParseResult; raises CsvFormatError when the file isn't UTF-8, isn't a CSV, has no header row, or lacks a required column.
    """
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvFormatError("The file is not UTF-8 text") from exc
    if "\x00" in text:
        raise CsvFormatError("The file is not a CSV")
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise CsvFormatError(f"The file is not a readable CSV: {exc}") from exc

    filled = [(number, cells) for number, cells in enumerate(records, start=1) if any(cell.strip() for cell in cells)]
    if not filled:
        raise CsvFormatError("The file has no header row")
    positions, ignored = _columns(filled[0][1])
    has_spec_section = "spec_section" in positions

    rows: list[ParsedRow] = []
    for number, cells in filled[1:]:
        raw = {name: cells[position].strip() if position < len(cells) else "" for name, position in positions.items()}
        row = ParsedRow(row=number, raw=raw)
        try:
            row.fields = _check_row(raw, has_spec_section)
        except PayItemValueError as exc:
            row.reason = str(exc)
        rows.append(row)

    _mark_duplicates(rows)
    return ParseResult(rows=rows, ignored_columns=ignored, has_spec_section=has_spec_section)


def _as_pay_item(fields: dict[str, Any]) -> dict[str, Any]:
    """
    Name a row's checked values as the API names a pay item's.
    Takes the values keyed by icid.contract_items column.
    Returns them keyed pay_item_ref, description, unit, quantity, unit_price, budget_code and spec_section.
    """
    return {
        "pay_item_ref": fields["item_no"],
        "description": fields["description"],
        "unit": fields["unit"],
        "quantity": fields["bid_quantity"],
        "unit_price": fields["bid_unit_price"],
        "budget_code": fields["budget_code"],
        "spec_section": fields.get("spec_section"),
    }


def classify_rows(project_id: str, parsed: ParseResult) -> Optional[ClassifyResult]:
    """
    Sort a file's rows into new items, overwrites of items the project already has under the same ref and budget code, and errors. The project's items are read once.
    Takes the project id and the file as parse_csv read it.
    Returns a ClassifyResult, each list in file order, or None if the project's items can't be read. An overwrite carries the item as it is and as it would be; without a spec_section column in the file it keeps the section it has.
    """
    current = list_project_pay_items(project_id)
    if current is None:
        return None
    by_key = {(item["pay_item_ref"], item["budget_code"]): item for item in current}

    result = ClassifyResult(
        parsed_rows=len(parsed.rows), ignored_columns=parsed.ignored_columns, has_spec_section=parsed.has_spec_section
    )
    for row in parsed.rows:
        if row.fields is None:
            result.errors.append({"row": row.row, "reason": row.reason, "raw": row.raw})
            continue
        result.to_write.append(row.fields)
        incoming = _as_pay_item(row.fields)
        existing = by_key.get((incoming["pay_item_ref"], incoming["budget_code"]))
        if existing is None:
            result.new.append({"row": row.row, **incoming})
            continue
        if not parsed.has_spec_section:
            incoming["spec_section"] = existing["spec_section"]
        result.overwrites.append({
            "row": row.row, "pay_item_ref": incoming["pay_item_ref"], "budget_code": incoming["budget_code"],
            "existing": existing, "incoming": incoming,
        })
    return result
