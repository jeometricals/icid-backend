"""
The Construction Report's Construction Measure (R1h): a property's claimed flags as DOT assesses them, one row per
surveyed thickness, at the project's DOT rates.

Per flag: its total is its gross area (computed_quantity); its assessable part is nothing for a flag surveyed
non-assessable, and otherwise its quantity (the override, or else its net area: the same quantity billing uses); its
non-assessable part is the rest, never below 0. A row's cost is its assessable square feet at its rate: 4" and 7"
assessed as 4" at cost_per_sf_4in, 7" at cost_per_sf_7in. Sums are kept exact; rounding is for display.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

ZERO = Decimal(0)

# The three rows, in the form's order: (surveyed_thickness, label, the rate it is assessed at)
MEASURE_ROWS = (
    ("4in", '4" Sidewalk Sq.Ft', "cost_per_sf_4in"),
    ("7in_as_4in", '7" Sidewalk Sq.Ft Assessed as 4"', "cost_per_sf_4in"),
    ("7in", '7" Sidewalk Sq.Ft', "cost_per_sf_7in"),
)


@dataclass
class MeasureRow:
    """One row of the Construction Measure: square feet in all, not assessable and assessable, the rate and the cost (None without a rate)."""

    thickness: str
    label: str
    total_sf: Decimal
    non_assessable_sf: Decimal
    assessable_sf: Decimal
    rate: Optional[Decimal]
    cost: Optional[Decimal]


@dataclass
class Measure:
    """The Construction Measure: its rows and the form's two estimates (square feet in all, and cost; None when no row has a rate)."""

    rows: list[MeasureRow]
    estimated_sf: Decimal
    estimated_cost: Optional[Decimal]


def flag_areas(flag: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    """
    Split one flag's area as DOT assesses it.
    Takes the flag (computed_quantity, quantity, surveyed_is_assessable).
    Returns (total, assessable, non-assessable) square feet.
    """
    total = Decimal(flag["computed_quantity"])
    assessable = Decimal(flag["quantity"]) if flag["surveyed_is_assessable"] else ZERO
    return total, assessable, max(total - assessable, ZERO)


def compute_measure(flags: list[dict[str, Any]], rates: dict[str, Optional[Decimal]]) -> Measure:
    """
    Work out a property's Construction Measure from the flags an IDR claimed on it.
    Takes the flags (surveyed_thickness, computed_quantity, quantity, surveyed_is_assessable) and the project's rates (cost_per_sf_4in, cost_per_sf_7in; None when not set).
    Returns the Measure: the three rows, every one listed, and the estimates.
    """
    rows = []
    for thickness, label, rate_key in MEASURE_ROWS:
        total = assessable = non_assessable = ZERO
        for flag in flags:
            if flag["surveyed_thickness"] == thickness:
                flag_total, flag_assessable, flag_non = flag_areas(flag)
                total += flag_total
                assessable += flag_assessable
                non_assessable += flag_non
        rate = rates.get(rate_key)
        rate = None if rate is None else Decimal(rate)
        rows.append(MeasureRow(thickness, label, total, non_assessable, assessable, rate,
                               None if rate is None else assessable * rate))
    costs = [row.cost for row in rows if row.cost is not None]
    return Measure(rows, sum((row.total_sf for row in rows), ZERO), sum(costs, ZERO) if costs else None)
