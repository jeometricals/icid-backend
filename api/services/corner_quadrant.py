"""
The corner-quadrant flag shape (Slice Y5): the OE sheet's area of a corner's sidewalk, an arc segment (A1) on a right
triangle less a correction rectangle at its inner corner, and its outline for drawing.
"""

import math
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

_CENTS = Decimal("0.01")
CORNER_QUADRANT = "corner_quadrant"
# A corner quadrant is measured on its own: never in a bundle
NOT_BUNDLED_MESSAGE = "corner_quadrant cannot be bundled"
# The OE sheet's arc-segment coefficients by H/C (0.01 to 0.50), verbatim: a segment's area is Coef x H x C
COEF_TABLE: dict[Decimal, Decimal] = {Decimal(ratio): Decimal(coef) for ratio, coef in (
    ("0.01", "0.6667"), ("0.02", "0.6669"), ("0.03", "0.6671"), ("0.04", "0.6675"), ("0.05", "0.6680"),
    ("0.06", "0.6686"), ("0.07", "0.6693"), ("0.08", "0.6701"), ("0.09", "0.6710"), ("0.10", "0.6720"),
    ("0.11", "0.6731"), ("0.12", "0.6743"), ("0.13", "0.6756"), ("0.14", "0.6770"), ("0.15", "0.6785"),
    ("0.16", "0.6801"), ("0.17", "0.6818"), ("0.18", "0.6836"), ("0.19", "0.6855"), ("0.20", "0.6875"),
    ("0.21", "0.6896"), ("0.22", "0.6918"), ("0.23", "0.6941"), ("0.24", "0.6965"), ("0.25", "0.6989"),
    ("0.26", "0.7014"), ("0.27", "0.7041"), ("0.28", "0.7068"), ("0.29", "0.7096"), ("0.30", "0.7125"),
    ("0.31", "0.7154"), ("0.32", "0.7185"), ("0.33", "0.7216"), ("0.34", "0.7248"), ("0.35", "0.7280"),
    ("0.36", "0.7314"), ("0.37", "0.7348"), ("0.38", "0.7383"), ("0.39", "0.7419"), ("0.40", "0.7455"),
    ("0.41", "0.7492"), ("0.42", "0.7530"), ("0.43", "0.7568"), ("0.44", "0.7607"), ("0.45", "0.7647"),
    ("0.46", "0.7687"), ("0.47", "0.7728"), ("0.48", "0.7769"), ("0.49", "0.7811"), ("0.50", "0.7854"),
)}
RATIO_RANGE_MESSAGE = "H/C must come to between 0.01 and 0.50 (the coefficient table's range)"
CORRECTION_TOO_LARGE_MESSAGE = "Correction rectangle exceeds arc + triangle - check inputs"
# How many steps the drawn arc takes (as an arc flag's)
ARC_STEPS = 32


@dataclass
class CornerQuadrant:
    """A corner quadrant worked out: H/C rounded, its coefficient, the arc segment, the triangle and the correction (exact), and the area (half up to the cent)."""

    ratio: Decimal
    coef: Decimal
    arc: Decimal
    triangle: Decimal
    correction: Decimal
    area: Decimal


def _value(dimensions: dict[str, Any], key: str) -> Decimal:
    """
    One of a corner quadrant's dimensions as an exact decimal, as typed.
    Takes the dimensions and the key.
    Returns the value.
    """
    return Decimal(str(dimensions[key]))


def hc_ratio(height: Decimal, chord: Decimal) -> Decimal:
    """
    H over C, to the hundredth, halves up (no interpolation: the OE sheet's practice).
    Takes H and C (C above 0).
    Returns the ratio.
    """
    return (height / chord).quantize(_CENTS, rounding=ROUND_HALF_UP)


def corner_quadrant(dimensions: dict[str, Any]) -> CornerQuadrant:
    """
    Work a corner quadrant out: Coef(round(H/C, 2)) x H x C + 0.5 x R1 x R2 - A x B, the total half up to the cent.
    Takes its dimensions {H, C, R1, R2, A, B} (H, C, R1, R2 above 0; A, B 0 or more).
    Returns its parts and area; raises ValueError when H/C falls outside the table or the area isn't above 0.
    """
    height, chord = _value(dimensions, "H"), _value(dimensions, "C")
    ratio = hc_ratio(height, chord)
    coef = COEF_TABLE.get(ratio)
    if coef is None:
        raise ValueError(RATIO_RANGE_MESSAGE)
    arc = coef * height * chord
    triangle = Decimal("0.5") * _value(dimensions, "R1") * _value(dimensions, "R2")
    correction = _value(dimensions, "A") * _value(dimensions, "B")
    area = (arc + triangle - correction).quantize(_CENTS, rounding=ROUND_HALF_UP)
    if area <= 0:
        raise ValueError(CORRECTION_TOO_LARGE_MESSAGE)
    return CornerQuadrant(ratio, coef, arc, triangle, correction, area)


def truncated(value: Decimal) -> str:
    """
    A part as the OE sheet prints it: cut (not rounded) to the cent.
    Takes the part.
    Returns it as text, two places.
    """
    return f"{value.quantize(_CENTS, rounding=ROUND_DOWN):.2f}"


def corner_quadrant_terms(dimensions: dict[str, Any]) -> str:
    """
    A corner quadrant's working as the Construction Report's calculations print it: Coef x H x C + 1/2 x R1 x R2 - A x B.
    Takes its dimensions.
    Returns the line's text (the correction left out when it is 0).
    """
    worked = corner_quadrant(dimensions)
    text = (f"{worked.coef}×{_length(dimensions['H'])}×{_length(dimensions['C'])}"
            f" + ½×{_length(dimensions['R1'])}×{_length(dimensions['R2'])}")
    if worked.correction:
        text += f" - {_length(dimensions['A'])}×{_length(dimensions['B'])}"
    return text


def _length(value: Any) -> str:
    """
    A length to two places, as typed.
    Takes the length.
    Returns it as text.
    """
    return f"{Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP):.2f}"


def corner_quadrant_outline(dimensions: dict[str, Any]) -> list[tuple[float, float]]:
    """
    A corner quadrant's outline in feet, in its own frame (x right, y down), as lib/flagGeometry.js draws it: the right
    angle at the bottom left, leg R1 along the bottom and R2 up the left side, an arc of height H bulging out of the
    hypotenuse, and an A x B notch cut at the right angle. For drawing and placing only: its area is the formula's.
    Takes its dimensions.
    Returns the points.
    """
    r1, r2 = float(dimensions["R1"]), float(dimensions["R2"])
    height = float(dimensions["H"])
    notch_a, notch_b = min(float(dimensions["A"]), r1), min(float(dimensions["B"]), r2)
    points: list[tuple[float, float]] = [(0.0, 0.0)]
    if notch_a > 0 and notch_b > 0:
        points += [(0.0, r2 - notch_b), (notch_a, r2 - notch_b), (notch_a, r2)]
    else:
        points.append((0.0, r2))
    points.append((r1, r2))
    # The arc from (R1, R2) back to (0, 0) over the hypotenuse, away from the right angle
    chord = math.hypot(r1, r2)
    ux, uy = r1 / chord, r2 / chord
    nx, ny = r2 / chord, -r1 / chord
    radius = (chord * chord / 4 + height * height) / (2 * height)
    half = math.atan2(chord / 2, radius - height)
    cx, cy = r1 / 2 - nx * (radius - height), r2 / 2 - ny * (radius - height)
    for step in range(1, ARC_STEPS):
        angle = half - 2 * half * step / ARC_STEPS
        points.append((cx + radius * (nx * math.cos(angle) + ux * math.sin(angle)),
                       cy + radius * (ny * math.cos(angle) + uy * math.sin(angle))))
    return points
