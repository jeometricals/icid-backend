"""
The Construction Report's Construction Measure (R1h): a property's claimed flags as DOT assesses them, one row per
surveyed thickness, at the project's DOT rates.

Per flag: its total is its gross area (computed_quantity); its assessable part is nothing for a flag surveyed
non-assessable, and otherwise its quantity (the override, or else its net area: the same quantity billing uses); its
non-assessable part is the rest, never below 0. A row's cost is its assessable square feet at its rate: 4" and 7"
assessed as 4" at cost_per_sf_4in, 7" at cost_per_sf_7in. Sums are kept exact; rounding is for display.

The calculations beside the sketch (R1h.3) show the same Measure piece by piece, per thickness: each flag's Length x
Width = Sub-Total (its gross area), the non-assessable pieces (flags surveyed non-assessable, and areas on the others),
and Total - Non-Assessable = Assessable. Their totals are the Measure's own, so the panel always agrees with box 2;
when the pieces don't add up to its non-assessable figure (an override, a feature subtracted from a flag, or an area
larger than its flag, which the Measure caps at the flag), an "Adjustment to box 2" row carries the difference.
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


@dataclass
class CalcRow:
    """One line of a calculations table: the piece's label (1, A1), its Length and Width as printed ('' when its shape has none) and its Sub-Total."""

    label: str
    length: str
    width: str
    sub_total: Decimal


@dataclass
class CalcGroup:
    """The calculations for one thickness: its flags and their total, its non-assessable pieces, any adjustment, and the Measure's non-assessable and assessable figures."""

    label: str
    rows: list[CalcRow]
    total: Decimal
    non_assessable_rows: list[CalcRow]
    adjustment: Decimal
    non_assessable: Decimal
    assessable: Decimal


def _length(value: Any) -> str:
    """
    A dimension in feet as the calculations print it: two decimal places.
    Takes the value.
    Returns the text.
    """
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def shape_terms(shape: Optional[str], dimensions: Optional[dict[str, Any]]) -> tuple[str, str]:
    """
    A piece's Length and Width as DOT's calculations print them, from its stored dimensions: a rectangle's L and W, a trapezoid's 0.5(a+b) and w, a triangle's 0.5(a) and b.
    Takes the shape and its dimensions.
    Returns (length, width); ('', '') for a polygon, an arc or no dimensions (the Sub-Total stands alone).
    """
    if not dimensions:
        return "", ""
    if shape == "rectangle":
        return _length(dimensions["L"]), _length(dimensions["W"])
    if shape == "trapezoid":
        return f"0.5({_length(dimensions['a'])}+{_length(dimensions['b'])})", _length(dimensions["w"])
    if shape == "triangle":
        return f"0.5({_length(dimensions['a'])})", _length(dimensions["b"])
    return "", ""


def _number(label: str) -> tuple[int, str]:
    """
    Sort key for a piece's label: 3 before 12, A2 before A10.
    Takes the label.
    Returns (its number, the label).
    """
    digits = "".join(ch for ch in label if ch.isdigit())
    return (int(digits) if digits else 0, label)


def calc_groups(flags: list[dict[str, Any]], measure: Measure, labels: dict[str, str]) -> list[CalcGroup]:
    """
    Lay out the calculations panel: per thickness with flags, in the Measure's order, every flag (Length x Width = Sub-Total), then the non-assessable pieces (a flag surveyed non-assessable whole, an area on an assessable flag), and the Measure's figures.
    Takes the flags (list_report_flags, their areas with id, shape, dimensions and computed_quantity), the Measure (compute_measure) and the labels (construction_sketch.number_items).
    Returns the groups; a thickness without flags has none.
    """
    groups = []
    for row in measure.rows:
        mine = [flag for flag in flags if flag["surveyed_thickness"] == row.thickness]
        if not mine:
            continue
        rows, pieces = [], []
        for flag in mine:
            length, width = shape_terms(flag["shape"], flag["dimensions"])
            rows.append(CalcRow(labels.get(f"flag:{flag['id']}", ""), length, width,
                                Decimal(flag["computed_quantity"])))
            if not flag["surveyed_is_assessable"]:
                pieces.append(CalcRow(labels.get(f"a:flag:{flag['id']}", ""), length, width,
                                      Decimal(flag["computed_quantity"])))
                continue
            for area in flag["areas"]:
                length, width = shape_terms(area.get("shape"), area.get("dimensions"))
                pieces.append(CalcRow(labels.get(f"a:area:{area['id']}", ""), length, width,
                                      Decimal(str(area["computed_quantity"]))))
        rows.sort(key=lambda calc: _number(calc.label))
        pieces.sort(key=lambda calc: _number(calc.label))
        listed = sum((piece.sub_total for piece in pieces), ZERO)
        groups.append(CalcGroup(row.label, rows, row.total_sf, pieces, row.non_assessable_sf - listed,
                                row.non_assessable_sf, row.assessable_sf))
    return groups
