"""
Reviewer edits: turning "this field, this new value" into one applied-and-logged edit.

A reviewer names a field by its field_path (see docs/data-model.md):
    header.<column>                 a header field of the IDR
    <key>.<key>...                  a field of a report, by the keys report_data stores
    <list>[<n>].<key>               a row of a list, by position from 0
    payItems[<item id>].<key>       a pay item's field, by the item's id

This module checks the path, finds the field and the value it holds now, and hands both to the statement in
api/queries/idr_field_edits.py, which only writes over that value. It also keeps an auto-generated General in step
with the reports it is built from, and assembles what the IDR endpoints return: the IDR, its reports and its edits.
"""

import re
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import ValidationError

from api.queries.idr_audit import last_action_time
from api.queries.idr_field_edits import (
    EDIT_STAGES,
    append_pay_item,
    apply_header_edit,
    apply_report_edit,
    list_field_edits,
    log_pay_item_approval,
)
from api.queries.idr_reports import get_general_report, get_idr_report, list_reports_for_idr
from api.queries.idrs import HEADER_COLUMNS, get_idr_by_id
from api.schemas.auth import UserOut
from api.schemas.idr import IdrHeaderUpdate
from api.schemas.idr_report import ReportType
from api.services.auto_general import regenerate_auto_general

# The report types whose form has a Pay Items table
PAY_ITEM_REPORT_TYPES = frozenset({ReportType.GEN.value, ReportType.SWCB.value, ReportType.AC.value})

# What starts a stage's round of review: the acceptance logged for it. Attestations made before the latest one
# belong to an earlier round and no longer count.
STAGE_ACCEPT_ACTIONS = {"stage1": "accept_stage1", "stage2": "accept_stage2"}

HEADER_PREFIX = "header."
PAY_ITEMS = "payItems"
PAY_QUANTITY = "payQuantity"
# One step of a report path: a key, optionally followed by [position] or, for payItems, [item id]
_SEGMENT = re.compile(r"([A-Za-z][A-Za-z0-9_]*)(?:\[([A-Za-z0-9-]+)\])?")


class FieldEditError(Exception):
    """An edit that can't be made; carries the HTTP status and the message for the reviewer."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _is_scalar(value: Any) -> bool:
    """
    Tell whether a value is one a single field holds.
    Takes the value.
    Returns True for None, text, a bool or a number; False for an object or a list.
    """
    return value is None or isinstance(value, (str, bool, int, float))


def parse_report_path(field_path: str) -> list[tuple[str, Optional[str]]]:
    """
    Split a report field_path into its steps.
    Takes the path, e.g. "workforce.foremen", "additionalWorkforce[0].count" or "payItems[<id>].payQuantity".
    Returns one (key, bracket) pair per step, bracket being the text between [ ] or None; raises FieldEditError (400) for a path that isn't in the grammar.
    """
    steps = []
    for part in field_path.split("."):
        match = _SEGMENT.fullmatch(part)
        if match is None:
            raise FieldEditError(400, f"Not a field path: {field_path}")
        steps.append((match.group(1), match.group(2)))
    return steps


def resolve_report_path(report_data: Any, field_path: str) -> tuple[list[str], Any]:
    """
    Find the field a path names inside a report's report_data.
    Takes the report_data and the field_path. A payItems step is looked up by the item's id; any other list by position.
    Returns (the path as report_data keys and list positions, the value there now); raises FieldEditError (400) when the path leads nowhere, names a whole object, list or pay item, or names a pay item's id.
    """
    steps = parse_report_path(field_path)
    json_path: list[str] = []
    value = report_data
    missing = FieldEditError(400, f"This report has no field {field_path}")

    for key, bracket in steps:
        if not isinstance(value, dict) or key not in value:
            raise missing
        value = value[key]
        json_path.append(key)
        if bracket is None:
            continue
        if not isinstance(value, list):
            raise missing
        if key == PAY_ITEMS and len(json_path) == 1:
            position = next((n for n, item in enumerate(value)
                             if isinstance(item, dict) and item.get("id") == bracket), None)
        else:
            position = int(bracket) if bracket.isdigit() and int(bracket) < len(value) else None
        if position is None:
            raise missing
        value = value[position]
        json_path.append(str(position))

    if not _is_scalar(value):
        raise FieldEditError(400, f"{field_path} is not a single field")
    if steps[0][0] == PAY_ITEMS and steps[-1][0] == "id":
        raise FieldEditError(400, "A pay item's id can't be edited")
    return json_path, value


def _is_pay_quantity(field_path: str) -> bool:
    """
    Tell whether a path names a pay item's quantity.
    Takes the field_path.
    Returns True for payItems[<id>].payQuantity.
    """
    steps = parse_report_path(field_path)
    return len(steps) == 2 and steps[0][0] == PAY_ITEMS and steps[0][1] is not None and steps[1] == (PAY_QUANTITY, None)


def _as_reviewer(user: UserOut) -> bool:
    """
    Tell whether the statement must check the editor is the stage's reviewer.
    Takes the editor.
    Returns False for an admin, who stands in for the reviewer; True for anyone else.
    """
    return user.role != "admin"


def _applied(rows: Optional[list[dict[str, Any]]]) -> dict[str, Any]:
    """
    Read an edit statement's result.
    Takes the rows it returned.
    Returns the edit row; raises FieldEditError (500) if the statement failed and (409) if the IDR or the field changed under it.
    """
    if rows is None:
        raise FieldEditError(500, "Failed to save the edit")
    if not rows:
        raise FieldEditError(409, "The IDR changed while you were editing; reload and try again")
    return rows[0]


def edit_header_field(idr: dict[str, Any], field_path: str, new_value: Any, user: UserOut) -> dict[str, Any]:
    """
    Apply a reviewer's change to one header field of an IDR in review.
    Takes the IDR row, the field_path ("header.<column>"), the new value as sent (a time as text, a number, text or None) and the editor.
    Returns the edit row; raises FieldEditError: 400 for a column that isn't a header field, a value the field can't take, temp_low above temp_high, or no change; 409 or 500 from the statement.
    """
    column = field_path.removeprefix(HEADER_PREFIX)
    if column not in HEADER_COLUMNS:
        raise FieldEditError(400, f"Not a header field: {field_path}")
    try:
        value = getattr(IdrHeaderUpdate.model_validate({column: new_value}), column)
    except ValidationError as exc:
        raise FieldEditError(400, f"{field_path}: {exc.errors()[0]['msg']}") from exc

    old_value = idr[column]
    if value == old_value:
        raise FieldEditError(400, "The field already holds that value")

    low = value if column == "temp_low" else idr["temp_low"]
    high = value if column == "temp_high" else idr["temp_high"]
    if low is not None and high is not None and low > high:
        raise FieldEditError(400, "temp_low cannot be greater than temp_high")

    return _applied(apply_header_edit(idr["idr_id"], column, old_value, value, user.uuid, idr["status"],
                                      _as_reviewer(user)))


def _editable_report(idr: dict[str, Any], report_id: UUID) -> dict[str, Any]:
    """
    Fetch a report a reviewer may edit.
    Takes the IDR row and the report uuid.
    Returns the report row; raises FieldEditError: 404 when the report isn't in the IDR, 400 for an auto-generated General (it is rebuilt from the reports it summarises, so those are what a reviewer edits).
    """
    report = get_idr_report(idr["idr_id"], report_id)
    if report is None:
        raise FieldEditError(404, "Report not found in this IDR")
    if report["is_auto_generated"]:
        raise FieldEditError(400, "An auto-generated General can't be edited; edit the report its content comes from")
    return report


def _refresh_auto_general(idr_id: UUID, report: dict[str, Any]) -> None:
    """
    Rebuild the IDR's auto-generated General after one of the reports it is built from was edited, so its merged pay items and description show the reviewer's value.
    Takes the IDR uuid and the report that was edited.
    Returns nothing; does nothing when the IDR has no auto-generated General, or the report is a General or an addendum.
    """
    if report["is_addendum"] or report["report_type"] == ReportType.GEN.value:
        return
    general = get_general_report(idr_id)
    if general is not None and general["is_auto_generated"]:
        regenerate_auto_general(idr_id)


def edit_report_field(idr: dict[str, Any], report_id: UUID, field_path: str, new_value: Any,
                      user: UserOut) -> dict[str, Any]:
    """
    Apply a reviewer's change to one field of a report, for an IDR in review. A pay item's payQuantity is recorded as a pay-item revision.
    Takes the IDR row, the report uuid, the field_path, the new value (text, a number, a bool or None) and the editor.
    Returns the edit row; raises FieldEditError: 404 for a report that isn't in the IDR; 400 for an auto-generated General, a path that isn't a field of the report, a new value that isn't a single value, or no change; 409 or 500 from the statement.
    """
    if not _is_scalar(new_value):
        raise FieldEditError(400, "A field takes a single value: text, a number, true or false, or nothing")
    report = _editable_report(idr, report_id)
    json_path, old_value = resolve_report_path(report["report_data"], field_path)
    if type(new_value) is type(old_value) and new_value == old_value:
        raise FieldEditError(400, "The field already holds that value")

    edit_type = "pay_item_revision" if _is_pay_quantity(field_path) else "field_change"
    edit = _applied(apply_report_edit(idr["idr_id"], report_id, field_path, json_path, edit_type, old_value,
                                      new_value, user.uuid, idr["status"], _as_reviewer(user)))
    _refresh_auto_general(idr["idr_id"], report)
    return edit


def _quantity_text(quantity: Any) -> str:
    """
    Read a quantity the way the pay-item form stores one: as text.
    Takes what was sent (text or a number).
    Returns the trimmed text; raises FieldEditError (400) when it is blank.
    """
    text = str(quantity).strip()
    if not text:
        raise FieldEditError(400, "A quantity is required")
    return text


def revise_pay_item(idr: dict[str, Any], pay_item_id: str, revised_quantity: Any, user: UserOut) -> dict[str, Any]:
    """
    Apply a reviewer's new quantity to one pay item, found by its id in whichever of the IDR's reports holds it.
    Takes the IDR row, the pay item's id, the new quantity and the editor.
    Returns the edit row; raises FieldEditError: 404 when no report of the IDR holds the item, 500 if the reports can't be read, and whatever edit_report_field raises.
    """
    quantity = _quantity_text(revised_quantity)
    reports = list_reports_for_idr(idr["idr_id"])
    if reports is None:
        raise FieldEditError(500, "Failed to load IDR reports")
    for report in reports:
        data = report["report_data"]
        items = data.get(PAY_ITEMS) if isinstance(data, dict) else None
        if isinstance(items, list) and any(isinstance(item, dict) and item.get("id") == pay_item_id for item in items):
            return edit_report_field(idr, report["report_id"], f"{PAY_ITEMS}[{pay_item_id}].{PAY_QUANTITY}", quantity,
                                     user)
    raise FieldEditError(404, "Pay item not found in this IDR")


def _same_quantity(a: Any, b: Any) -> bool:
    """
    Tell whether two quantities are the same amount, however they are written ("55" and "55.00" are).
    Takes the two values (text or numbers; None and blank count as nothing).
    Returns True when both are nothing, or both parse to the same number, or their text is identical.
    """
    left, right = ("" if v is None else str(v).strip() for v in (a, b))
    if left == right:
        return True
    try:
        return float(left) == float(right)
    except ValueError:
        return False


def _pay_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Read the pay items a reviewer attests to in a report: those of a report that isn't an auto-generated General, each carrying an id.
    Takes the report row.
    Returns the items, in the report's order (none for an auto-generated General, or a report without a payItems list).
    """
    data = report["report_data"]
    items = data.get(PAY_ITEMS) if isinstance(data, dict) else None
    if report["is_auto_generated"] or not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _touched(item: dict[str, Any], report_id: UUID, edits: list[dict[str, Any]]) -> bool:
    """
    Tell whether one of a reviewer's edits attests to a pay item as it now stands: an approval or a revision whose quantity is the item's current one, or the edit that added it.
    Takes the item, its report's uuid and the reviewer's edits at the stage, in the current round.
    Returns True when one does. An attestation to a quantity the item no longer has doesn't count.
    """
    item_path = f"{PAY_ITEMS}[{item['id']}]"
    quantity = item.get(PAY_QUANTITY)
    for edit in edits:
        if edit["report_id"] != report_id:
            continue
        kind, path, value = edit["edit_type"], edit["field_path"], edit["new_value"]
        if kind == "pay_item_approve" and path == item_path and _same_quantity(value, quantity):
            return True
        if kind == "pay_item_revision" and path == f"{item_path}.{PAY_QUANTITY}" and _same_quantity(value, quantity):
            return True
        if kind == "pay_item_add" and path == item_path and isinstance(value, dict) \
                and _same_quantity(value.get(PAY_QUANTITY), quantity):
            return True
    return False


def _attestations(idr: dict[str, Any], user: UserOut) -> Optional[list[dict[str, Any]]]:
    """
    List the edits by which a user has attested to pay items at the IDR's current stage, in its current round: theirs, stamped with this stage, made since the stage was last accepted.
    Takes the IDR row (in review) and the user.
    Returns the edits, or None if the IDR's edits can't be read.
    """
    edits = list_field_edits(idr["idr_id"])
    if edits is None:
        return None
    stage = EDIT_STAGES[idr["status"]][0]
    since = last_action_time(idr["idr_id"], STAGE_ACCEPT_ACTIONS[stage])
    return [edit for edit in edits
            if edit["editor_uuid"] == user.uuid and edit["editor_stage"] == stage
            and (since is None or edit["edited_at"] >= since)]


def untouched_pay_items(idr: dict[str, Any], user: UserOut) -> list[dict[str, Any]]:
    """
    List the pay items a user still has to approve, revise or add before they can approve the IDR's current stage. Every pay item of every report but an auto-generated General needs one of the three from them, at this stage, since it was last accepted, for the quantity the item has now. An admin standing in for the reviewer is held to the same.
    Takes the IDR row (in review) and the user about to approve the stage.
    Returns one dict per untouched item (pay_item_id, report_id, item_no, budget_code), in report and item order; raises FieldEditError (500) if the reports or the edits can't be read.
    """
    reports = list_reports_for_idr(idr["idr_id"])
    attestations = _attestations(idr, user)
    if reports is None or attestations is None:
        raise FieldEditError(500, "Failed to check the IDR's pay items")
    return [
        {"pay_item_id": item["id"], "report_id": report["report_id"], "item_no": item.get("itemNo") or None,
         "budget_code": item.get("budgetCode") or None}
        for report in reports for item in _pay_items(report)
        if not _touched(item, report["report_id"], attestations)
    ]


def untouched_message(count: int) -> str:
    """
    Word the refusal to approve a stage with pay items still waiting on its reviewer.
    Takes how many are waiting (at least one).
    Returns the message.
    """
    items = "1 pay item still needs" if count == 1 else f"{count} pay items still need"
    return f"{items} your approval or revision before you can approve this IDR"


def approve_pay_item(idr: dict[str, Any], pay_item_id: str, user: UserOut) -> Optional[dict[str, Any]]:
    """
    Record a reviewer's approval of one pay item as it stands, found by its id in whichever of the IDR's reports holds it. Approving an item they have already approved, revised or added at this stage (for the quantity it has now) changes nothing.
    Takes the IDR row, the pay item's id and the editor.
    Returns the edit row, or None when the item was already attested to; raises FieldEditError: 404 when no report of the IDR holds the item, 400 for an item of an auto-generated General, 500 if the reports or edits can't be read, 409 or 500 from the statement.
    """
    reports = list_reports_for_idr(idr["idr_id"])
    attestations = _attestations(idr, user)
    if reports is None or attestations is None:
        raise FieldEditError(500, "Failed to load IDR reports")
    for report in reports:
        data = report["report_data"]
        items = data.get(PAY_ITEMS) if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for position, item in enumerate(items):
            if not isinstance(item, dict) or item.get("id") != pay_item_id:
                continue
            if report["is_auto_generated"]:
                raise FieldEditError(400, "An auto-generated General's pay items are approved on the reports they come from")
            if _touched(item, report["report_id"], attestations):
                return None
            return _applied(log_pay_item_approval(
                idr["idr_id"], report["report_id"], f"{PAY_ITEMS}[{pay_item_id}]",
                [PAY_ITEMS, str(position), PAY_QUANTITY], item.get(PAY_QUANTITY), user.uuid, idr["status"],
                _as_reviewer(user)))
    raise FieldEditError(404, "Pay item not found in this IDR")


def add_pay_item(idr: dict[str, Any], report_id: UUID, item_no: str, budget_code: str, quantity: Any, unit: str,
                 description: str, user: UserOut) -> dict[str, Any]:
    """
    Add a pay item to one report of an IDR in review, on the reviewer's behalf.
    Takes the IDR row, the report uuid, the item's fields and the editor. The item gets a fresh id.
    Returns the edit row; raises FieldEditError: 404 for a report that isn't in the IDR; 400 for an auto-generated General, a report type without pay items, a blank quantity, or an item with neither an item number nor a description; 409 or 500 from the statement.
    """
    report = _editable_report(idr, report_id)
    if report["report_type"] not in PAY_ITEM_REPORT_TYPES:
        raise FieldEditError(400, "This kind of report has no pay items")
    item = {"id": str(uuid4()), "itemNo": item_no.strip(), "budgetCode": budget_code.strip(),
            "payQuantity": _quantity_text(quantity), "unit": unit.strip(), "description": description.strip()}
    if not item["itemNo"] and not item["description"]:
        raise FieldEditError(400, "A pay item needs an item number or a description")

    edit = _applied(append_pay_item(idr["idr_id"], report_id, item, user.uuid, idr["status"], _as_reviewer(user)))
    _refresh_auto_general(idr["idr_id"], report)
    return edit


def edit_field(idr_id: UUID, report_id: Optional[UUID], field_path: str, new_value: Any, user: UserOut) -> dict[str, Any]:
    """
    Apply a reviewer's change to one field: a header field when there is no report, otherwise a field of that report.
    Takes the IDR uuid, the report uuid (None for a header field), the field_path, the new value and the editor.
    Returns the edit row; raises FieldEditError: 404 for an IDR that doesn't exist, 400 when it isn't in review or the path and the report don't go together, and whatever the field's own edit raises.
    """
    idr = editable_idr(idr_id)
    is_header = field_path.startswith(HEADER_PREFIX)
    if is_header != (report_id is None):
        raise FieldEditError(400, "A header field takes no report_id; any other field needs one")
    if is_header:
        return edit_header_field(idr, field_path, new_value, user)
    return edit_report_field(idr, report_id, field_path, new_value, user)


def editable_idr(idr_id: UUID) -> dict[str, Any]:
    """
    Fetch an IDR a reviewer is editing.
    Takes the IDR uuid.
    Returns the IDR row; raises FieldEditError: 404 when there is none, 400 when it isn't in review (the routes' stage_reviewer dependency refuses both first; this covers a change in between).
    """
    idr = get_idr_by_id(idr_id)
    if idr is None:
        raise FieldEditError(404, "IDR not found")
    if idr["status"] not in EDIT_STAGES or idr.get("deleted_at") is not None:
        raise FieldEditError(400, "Only an IDR in review can be edited by a reviewer")
    return idr


def _initials(first_name: Optional[str], last_name: Optional[str]) -> str:
    """
    Make an editor's initials: the first letter of their first name and of their last name.
    Takes the two names (either may be missing).
    Returns the initials in capitals, e.g. "OE"; one letter, or none, when names are missing.
    """
    return "".join(name.strip()[0].upper() for name in (first_name, last_name) if name and name.strip())


def field_edits_for(idr_id: UUID) -> Optional[list[dict[str, Any]]]:
    """
    List an IDR's edits, oldest first, each with its editor's name and initials.
    Takes the IDR uuid.
    Returns the edits as dicts ready for the FieldEdit schema (empty if there are none), or None if they can't be read.
    """
    rows = list_field_edits(idr_id)
    if rows is None:
        return None
    return [{**row,
             "editor_name": " ".join(n for n in (row.get("editor_first_name"), row.get("editor_last_name")) if n) or None,
             "editor_initials": _initials(row.get("editor_first_name"), row.get("editor_last_name"))}
            for row in rows]
