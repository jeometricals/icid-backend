"""
The pay item a non-assessable flag takes by default (pilot sweep #2): the contractor builds a non-assessable flag like any
other, so it bills under the sidewalk item of its thickness. Every way a flag becomes non-assessable without a pay item
applies it: its create and PATCH (default_pay_item_id), the Smart PIR import and the sidewalk strip's auto-flip.
Migration 064 gave the flags drawn before this theirs.
"""

import logging
from typing import Any, Optional
from uuid import UUID

from api.queries.contract_items import list_project_pay_items_by_number
from api.services.pay_item_units import flag_unit

logger = logging.getLogger(__name__)

# The DDC sidewalk item of each surveyed thickness; a 7" assessed as 4" takes the 4" item
THICKNESS_PAY_ITEMS = {"4in": "4.13 AAS", "7in": "4.13 BAS", "7in_as_4in": "4.13 AAS"}


def sole_pay_item(rows: list[dict[str, Any]], item_no: str) -> Optional[UUID]:
    """
    Pick a project's one contract item under an item number, whatever its budget code, in a unit a flag can be measured in.
    Takes the project's pay items (as list_project_pay_items returns them; others' item numbers are ignored) and the number.
    Returns its id, or None when the project has none under it, several, or one in a unit a flag can't be measured in.
    """
    matching = [row for row in rows if row["pay_item_ref"] == item_no]
    if len(matching) != 1 or flag_unit(matching[0]["unit"]) is None:
        return None
    return matching[0]["pay_item_uuid"]


def default_pay_items(rows: list[dict[str, Any]]) -> dict[str, UUID]:
    """
    The pay item a non-assessable flag of each thickness takes by default (sole_pay_item of THICKNESS_PAY_ITEMS).
    Takes all of a project's pay items (as list_project_pay_items returns them).
    Returns {thickness: pay item id}, a thickness left out when the project has no one item for it.
    """
    picked = {thickness: sole_pay_item(rows, item_no) for thickness, item_no in THICKNESS_PAY_ITEMS.items()}
    return {thickness: pay_item for thickness, pay_item in picked.items() if pay_item is not None}


def default_pay_item_id(project_id: str, thickness: Optional[str]) -> Optional[UUID]:
    """
    Find the pay item a non-assessable flag of a thickness takes by default: the project's one contract item under the
    thickness's item number (THICKNESS_PAY_ITEMS), whatever its budget code, in a unit a flag can be measured in.
    Takes the project id and the flag's surveyed thickness.
    Returns the pay item's id, or None when the project has none or several (the flag stays unmapped, and Progress counts
    it for the OE) or they can't be read.
    """
    item_no = THICKNESS_PAY_ITEMS.get(thickness or "")
    if item_no is None:
        return None
    rows = list_project_pay_items_by_number(project_id, item_no)
    if rows is None:
        logger.warning("Default pay item not read: project_id=%s item_no=%s", project_id, item_no)
        return None
    return sole_pay_item(rows, item_no)
