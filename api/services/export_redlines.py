"""
Reviewer redlines for the IDR export: an IDR's edits (icid.idr_field_edits), read as what a page prints.

An edit is applied to the IDR when it is made, so the IDR holds each field's current value and the edits hold its
history. A field's chain is: the value before its first edit (that edit's old_value), struck through; then each
edit's new value, by its reviewer; only the last entry is left standing. When the field's current value isn't the
last edit's (the inspector changed it after a return), every edit is struck and the current value closes the chain.

A pay item's chain is a row each: the item's own row with the quantity it had, then one row per revision. Each row's
initials (whoever added or revised it) print in its Quantity Chk cell. Approvals add theirs to the row that stands,
and only those given for the quantity the item has now.

This module decides what is printed; export_common turns it into cells.
"""

from dataclasses import dataclass, replace
from typing import Any, Callable, Optional
from uuid import UUID

from api.services.field_edits import HEADER_PREFIX, PAY_ITEMS, PAY_QUANTITY, same_quantity

# What a chain shows for an edit that emptied the field
REDLINE_BLANK = "(blank)"


@dataclass(frozen=True)
class RedlineEntry:
    """One value in a field's chain: its text, whose it is, and whether a later value replaced it."""

    text: str
    by_reviewer: bool               # a reviewer's edit (printed in the redline colour); False for the inspector's
    initials: Optional[str] = None  # the reviewer's
    struck: bool = False
    revised: bool = False           # the inspector's value after the last edit, closing the chain


@dataclass(frozen=True)
class QuantityLine:
    """One row of a pay item's chain: a quantity, the initials for its Quantity Chk cell, and how it is marked."""

    quantity: Optional[str]
    item_row: bool                  # the item's own row (it carries the description); False for a revision's row
    by_reviewer: bool               # a reviewer's quantity, or an item a reviewer added
    initials: tuple[str, ...] = ()  # who added or revised it, then who approved it
    struck: bool = False
    revised: bool = False


def _text(value: Any) -> Optional[str]:
    """
    Read a value as the text a chain shows.
    Takes the value.
    Returns the trimmed text, or None when blank or missing.
    """
    text = str(value).strip() if value is not None else ""
    return text or None


def redline_chain(edits: list[dict[str, Any]], current: Any, show: Callable[[Any], Any] = _text,
                  same: Optional[Callable[[Any, Any], bool]] = None) -> list[RedlineEntry]:
    """
    Lay one field's edits out as its chain (see the module's docstring).
    Takes the field's edits, oldest first, its current value, a function giving the text a value prints as (None for
    blank), and one telling whether two printed values are the same (plain equality unless given).
    Returns the entries in order, none when the field was never edited. A blank original has no entry; an edit that
    emptied the field shows REDLINE_BLANK.
    """
    if not edits:
        return []

    def shown(value: Any) -> Optional[str]:
        return _text(show(value))

    entries = []
    original = shown(edits[0]["old_value"])
    if original:
        entries.append(RedlineEntry(original, by_reviewer=False, struck=True))
    for edit in edits:
        entries.append(RedlineEntry(shown(edit["new_value"]) or REDLINE_BLANK, by_reviewer=True,
                                    initials=_text(edit.get("editor_initials")), struck=True))
    last, now = shown(edits[-1]["new_value"]), shown(current)
    if last == now or (same is not None and same(last, now)):
        entries[-1] = replace(entries[-1], struck=False)
    else:
        entries.append(RedlineEntry(now or REDLINE_BLANK, by_reviewer=False, revised=True))
    return entries


class Redlines:
    """An IDR's edits as one report's pages need them: the header's, and that report's own."""

    def __init__(self, edits: Optional[list[dict[str, Any]]] = None, report_id: Optional[UUID] = None) -> None:
        """
        Sort an IDR's edits into the header's and one report's.
        Takes the edits, oldest first (each with editor_initials), and the report's uuid (None keeps the header's
        only, as for an auto-generated General, whose content can't be edited).
        Returns nothing.
        """
        self._edits = list(edits or [])
        self._header = [e for e in self._edits if e.get("report_id") is None]
        self._report = [e for e in self._edits
                        if report_id is not None and e.get("report_id") is not None
                        and str(e["report_id"]) == str(report_id)]

    def for_report(self, report_id: Optional[UUID]) -> "Redlines":
        """
        Give the same IDR's redlines for another of its reports.
        Takes the report's uuid (None for the header's edits only).
        Returns a new Redlines.
        """
        return Redlines(self._edits, report_id)

    def header(self, column: str) -> list[dict[str, Any]]:
        """
        Find the edits made to one header field.
        Takes the header column, e.g. "weather_am".
        Returns its edits, oldest first (empty when it was never edited).
        """
        return [e for e in self._header if e["field_path"] == f"{HEADER_PREFIX}{column}"]

    def field(self, field_path: str) -> list[dict[str, Any]]:
        """
        Find the edits made to one field of the report.
        Takes the field_path as the edits store it, e.g. "workforce.foremen".
        Returns its edits, oldest first (empty when it was never edited).
        """
        return [e for e in self._report if e["field_path"] == field_path]

    def pay_item(self, item: dict[str, Any]) -> Optional[list[QuantityLine]]:
        """
        Lay one pay item's edits out as the rows it prints on.
        Takes the pay item as the report holds it (with its id and current quantity).
        Returns its rows in order: its own row, then one per revision, and the inspector's quantity last when they
        changed it after the last revision; None for an item no reviewer added, revised or approved as it stands.
        """
        item_id = item.get("id")
        if not item_id or not self._report:
            return None
        path = f"{PAY_ITEMS}[{item_id}]"
        current = _text(item.get(PAY_QUANTITY))
        revisions = [e for e in self._report
                     if e["edit_type"] == "pay_item_revision" and e["field_path"] == f"{path}.{PAY_QUANTITY}"]
        added = next((e for e in self._report if e["edit_type"] == "pay_item_add" and e["field_path"] == path), None)
        approvers = [_text(e.get("editor_initials")) for e in self._report
                     if e["edit_type"] == "pay_item_approve" and e["field_path"] == path
                     and same_quantity(e["new_value"], current)]
        if not revisions and added is None and not approvers:
            return None

        adder = _text(added.get("editor_initials")) if added is not None else None
        own = QuantityLine(current, item_row=True, by_reviewer=added is not None, initials=(adder,) if adder else ())
        chain = redline_chain(revisions, current, same=same_quantity)
        if not chain:
            lines = [own]
        else:
            # The item's own row holds the quantity it had before its first revision (none, when it had none)
            first = chain[0] if not chain[0].by_reviewer else None
            lines = [replace(own, quantity=first.text if first else None, struck=True)]
            lines += [QuantityLine(entry.text, item_row=False, by_reviewer=entry.by_reviewer,
                                   initials=(entry.initials,) if entry.initials else (), struck=entry.struck,
                                   revised=entry.revised)
                      for entry in chain if entry is not first]
        standing = lines[-1]
        extra = tuple(dict.fromkeys(a for a in approvers if a and a not in standing.initials))
        lines[-1] = replace(standing, initials=standing.initials + extra)
        return lines


NO_REDLINES = Redlines()
