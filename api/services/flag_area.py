"""
An area from its shape and dimensions, in square feet: what the API stores as the computed_quantity of a flag, a
non-assessable area or a sized feature. Dimensions are feet, checked per shape by api/schemas/flag.py before they get
here.
"""

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from api.services.corner_quadrant import corner_quadrant

# Areas are stored to the cent of a square foot
_CENTS = Decimal("0.01")


def _number(value: Any) -> Decimal:
    """
    Turn one dimension into an exact decimal, as it was written.
    Takes a number (int, float or Decimal).
    Returns the Decimal.
    """
    return value if isinstance(value, Decimal) else Decimal(str(value))


def polygon_area(vertices: list[dict[str, Any]]) -> Decimal:
    """
    The area a polygon encloses, by the shoelace formula, its vertices taken in order; unrounded and never negative.
    Takes the vertices, each {x, y}.
    Returns the area (0 for a degenerate polygon, such as collinear points).
    """
    points = [(_number(vertex["x"]), _number(vertex["y"])) for vertex in vertices]
    twice = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
    return abs(twice) / 2


def compute_area(shape: str, dimensions: dict[str, Any]) -> Decimal:
    """
    Compute a shape's area: rectangle L x W; trapezoid (a + b) / 2 x w; right triangle a x b / 2; polygon by the
    shoelace formula; arc the circular segment r^2 / 2 x (theta - sin theta); circle pi r^2 (features only); a corner
    quadrant Coef(H/C) x H x C + R1 x R2 / 2 - A x B (flags only, Slice Y5).
    Takes the shape and its dimensions (already checked).
    Returns the area in square feet, rounded to 2 decimal places, halves up; raises ValueError for an unknown shape.
    """
    if shape == "rectangle":
        area = _number(dimensions["L"]) * _number(dimensions["W"])
    elif shape == "trapezoid":
        area = (_number(dimensions["a"]) + _number(dimensions["b"])) / 2 * _number(dimensions["w"])
    elif shape == "triangle":
        area = _number(dimensions["a"]) * _number(dimensions["b"]) / 2
    elif shape == "polygon":
        area = polygon_area(dimensions["vertices"])
    elif shape == "arc":
        r, theta = float(dimensions["r"]), float(dimensions["theta"])
        area = Decimal(repr(r * r / 2 * (theta - math.sin(theta))))
    elif shape == "circle":
        r = float(dimensions["r"])
        area = Decimal(repr(math.pi * r * r))
    elif shape == "corner_quadrant":
        area = corner_quadrant(dimensions).area
    else:
        raise ValueError(f"Unknown shape: {shape}")
    return area.quantize(_CENTS, rounding=ROUND_HALF_UP)
