"""
Progress as CSV (Slice ML2): the flag Progress table's rows, verbatim, for sorting and pivoting in a spreadsheet. A row
per pay item of a rollup (compute_progress's), led by its Scope: the project, a task order's number, or a site's address.
No values, totals or headline: what the table shows.
"""

from decimal import Decimal
from typing import Any, Optional

from api.services.budget_rollup import money
from api.services.csv_files import csv_bytes, file_token, today_in_new_york

COLUMNS = ("Scope", "Item No", "Budget Code", "Unit", "Description", "Approved Qty", "Approved Count", "In Flight Qty",
           "In Flight Count", "Unclaimed Qty", "Unclaimed Count", "Live Total Qty", "Live Total Count", "% Approved")
# The table's four states, in its column order
STATES = ("approved", "in_flight", "unclaimed", "live_total")


def _quantity(value: Optional[float]) -> str:
    """
    A quantity as the CSV writes it: to the hundredth, halves up.
    Takes the quantity (None: none).
    Returns the text; '' for None.
    """
    return "" if value is None else f"{money(Decimal(str(value))):.2f}"


def _percent(value: Optional[float]) -> str:
    """
    The approved share as the CSV writes it: to the tenth, as the rollup rounded it.
    Takes the percent (None when nothing is live).
    Returns the text; '' for None.
    """
    return "" if value is None else f"{Decimal(str(value)).quantize(Decimal('0.1'))}"


def scope_lines(scope: str, pay_items: list[dict[str, Any]]) -> list[list[str]]:
    """
    One rollup's rows: a row per pay item, its quantities and counts in each state and its approved share.
    Takes the scope's name (the project, a task order's number or a site's address) and the rollup's pay_items.
    Returns the rows as text.
    """
    return [[scope, item["item_no"], item["budget_code"], item["unit"], item["description"],
             *(cell for state in STATES for cell in (_quantity(item[state]["quantity"]), str(item[state]["flag_count"]))),
             _percent(item["approved_pct"])] for item in pay_items]


def project_progress_csv(scope: str, rollup: dict[str, Any]) -> bytes:
    """
    The Progress page's view as a CSV: the rollup's rows under one Scope.
    Takes the scope's name (the project id, the task order's number or the site's address) and the rollup (compute_progress's).
    Returns the file's bytes (the header alone when there are no rows).
    """
    return csv_bytes(list(COLUMNS), scope_lines(scope, rollup["pay_items"]))


def task_order_progress_csv(task_order_number: str, rollup: dict[str, Any]) -> bytes:
    """
    The task order page's view as a CSV: the task order's rows, then each bundled site's, in its order.
    Takes the task order's number and its rollup (compute_progress's, with properties).
    Returns the file's bytes.
    """
    lines = scope_lines(task_order_number, rollup["pay_items"])
    for site in rollup["properties"]:
        lines += scope_lines(site["display_address"], site["pay_items"])
    return csv_bytes(list(COLUMNS), lines)


def progress_filename(project_id: str, scope: Optional[str] = None) -> str:
    """
    A Progress CSV's file name: the project, the scope (a task order's number, or 'site') when narrowed, and today in New York.
    Takes the project id and the scope's token (None: the whole project).
    Returns '<project_id>_progress_<YYYY-MM-DD>.csv' or '<project_id>_progress_<scope>_<YYYY-MM-DD>.csv'.
    """
    middle = f"_{file_token(scope)}" if scope else ""
    return f"{file_token(project_id)}_progress{middle}_{today_in_new_york()}.csv"
