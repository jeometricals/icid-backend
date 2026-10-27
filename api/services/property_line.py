"""
A site's frontage and its sidewalk strip (Slice Y1.5).

The sidewalk strip is the sidewalk in front of the lot, drawn on a PIR page as a closed polygon of {x, y} points in the
page's native pixels: the property line first (points 0 -> 1), then the lot's side lines out to the curb and the curb.
Its first edge, at the page's scale, is the strip's frontage. A flag whose outline lies wholly outside the strip is
outside the property line; the strip route marks it non-assessable.

effective_frontage resolves the frontage Y2's billing clip and the Construction Report use: the strip's, else the
page's tax-map width (Y1), else the site's MapPLUTO frontage (Y1), else none.
"""

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from api.services.area_containment import overlap_area
from api.services.construction_sketch import placed_outline

Point = tuple[float, float]

CENT = Decimal("0.01")
# Most points a strip may have
MAX_STRIP_POINTS = 200
# Overlap below this (square feet) is no overlap: a flag that only touches the strip's edge is outside
OUTSIDE_TOLERANCE_SF = 0.01
# A flag less than this fraction outside its strip is wholly inside it: no clip (Slice Y2)
OUTSIDE_FRACTION_MIN = 0.001
# What effective_frontage says its figure came from, but for the tax-map sources, which are passed through as stored
STRIP_LABEL = "sidewalk_strip"
UNAVAILABLE = "unavailable"


class StripError(ValueError):
    """A sidewalk strip that isn't a simple polygon of 3 to MAX_STRIP_POINTS points with some area."""


def strip_points(raw: list[dict[str, Any]]) -> list[Point]:
    """
    Read a strip's points as the API and the database hold them.
    Takes the [{x, y}, ...] list.
    Returns the points as (x, y) floats.
    """
    return [(float(point["x"]), float(point["y"])) for point in raw]


def _signed_area(points: list[Point]) -> float:
    """
    The shoelace area of a polygon, positive when its points run anticlockwise (in a y-up frame).
    Takes the points, in order.
    Returns the signed area, in the points' units squared.
    """
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])) / 2


def _edges_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    """
    Whether two segments meet, touching included.
    Takes the first segment's ends and the second's.
    Returns True when they share a point.
    """
    def side(p: Point, q: Point, r: Point) -> float:
        """The turn from p to q to r: positive left, negative right, 0 in line."""
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def within(p: Point, q: Point, r: Point) -> bool:
        """Whether r, in line with p and q, lies between them."""
        return min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])

    d1, d2, d3, d4 = side(c, d, a), side(c, d, b), side(a, b, c), side(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return any(turn == 0 and within(p, q, r) for turn, p, q, r in
               ((d1, c, d, a), (d2, c, d, b), (d3, a, b, c), (d4, a, b, d)))


def check_strip(points: list[Point]) -> None:
    """
    Refuse a strip that isn't a simple polygon: 3 to MAX_STRIP_POINTS points, finite, its first edge of some length,
    some area, and no edge meeting another but its neighbours at their shared point.
    Takes the points, in order (the polygon closes from the last back to the first).
    Returns nothing; raises StripError naming the problem.
    """
    if not 3 <= len(points) <= MAX_STRIP_POINTS:
        raise StripError(f"A sidewalk strip has 3 to {MAX_STRIP_POINTS} points")
    if not all(math.isfinite(value) for point in points for value in point):
        raise StripError("A sidewalk strip's points must be numbers")
    if points[0] == points[1]:
        raise StripError("A sidewalk strip's first edge, the property line, has no length")
    if abs(_signed_area(points)) < 1e-6:
        raise StripError("A sidewalk strip must enclose some area")
    edges = list(zip(points, points[1:] + points[:1]))
    for i, (a, b) in enumerate(edges):
        for j in range(i + 1, len(edges)):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue  # neighbours share a point
            if _edges_cross(a, b, *edges[j]):
                raise StripError("A sidewalk strip's edges must not cross")


def strip_frontage_ft(points: list[Point], scale: Optional[float]) -> Optional[Decimal]:
    """
    The strip's frontage: the length of its first edge, the property line, at the page's scale.
    Takes the points and the page's px/ft (None for none).
    Returns feet to the cent, halves up; None without a scale.
    """
    if not scale:
        return None
    (x1, y1), (x2, y2) = points[0], points[1]
    return Decimal(str(math.hypot(x2 - x1, y2 - y1) / float(scale))).quantize(CENT, rounding=ROUND_HALF_UP)


def outside_flags(points: list[Point], flags: list[dict[str, Any]], scale: float) -> list[dict[str, Any]]:
    """
    The flags that lie wholly outside a strip: their outline on the page overlaps it by no more than
    OUTSIDE_TOLERANCE_SF. A flag without a place on the page, or without a shape to draw, can't be told, and isn't one.
    Takes the strip's points, the page's flags (shape, dimensions, sketch_x, sketch_y, rotation) and its px/ft.
    Returns those flags, in the order given.
    """
    scale = float(scale)
    outside = []
    for flag in flags:
        outline = placed_outline(flag, scale)
        if len(outline) < 3:
            continue
        if overlap_area(outline, points) / (scale * scale) <= OUTSIDE_TOLERANCE_SF:
            outside.append(flag)
    return outside


def flag_clip(flag: dict[str, Any]) -> Optional[dict[str, Decimal]]:
    """
    Work out the part of a claimed flag outside its page's sidewalk strip, which the homeowner isn't assessed for
    (Slice Y2): its quantity (the override, or else its net area) times the fraction of its outline outside the strip,
    to the cent, halves up. A flag surveyed non-assessable, without a place or a shape to draw, on a page without a scale
    or a strip, or (nearly) wholly inside the strip (under 0.1% outside) has no clip.
    Takes the flag (shape, dimensions, sketch_x, sketch_y, rotation, surveyed_is_assessable, quantity, scale_px_per_ft,
    sidewalk_strip_points; as assessment_clip_audits.list_clip_inputs reads it).
    Returns {pre_clip_sf, clipped_sf}, or None for no clip.
    """
    scale, strip = flag.get("scale_px_per_ft"), flag.get("sidewalk_strip_points")
    if not flag.get("surveyed_is_assessable") or not scale or not strip:
        return None
    outline = placed_outline(flag, float(scale))
    if len(outline) < 3:
        return None
    area = abs(_signed_area(outline))
    if area <= 0:
        return None
    outside = max(0.0, 1.0 - overlap_area(outline, strip_points(strip)) / area)
    if outside < OUTSIDE_FRACTION_MIN:
        return None
    quantity = Decimal(str(flag["quantity"])).quantize(CENT, rounding=ROUND_HALF_UP)
    clipped = min((quantity * Decimal(str(outside))).quantize(CENT, rounding=ROUND_HALF_UP), quantity)
    if clipped <= 0:
        return None
    return {"pre_clip_sf": quantity, "clipped_sf": clipped}


def flag_clips(flags: list[dict[str, Any]], with_idr: bool = False) -> list[dict[str, Any]]:
    """
    Work out the clips of the flags an approval, an un-delete or a re-clip handles (Slice Y2), as the statements take them.
    Takes the flags (list_clip_inputs' rows) and whether each clip names its flag's claiming IDR (a re-clip's).
    Returns [{flag_id, (idr_id,) pre_clip_sf, clipped_sf}] for the flags that have a clip.
    """
    clips = []
    for flag in flags:
        clip = flag_clip(flag)
        if clip is not None:
            clips.append({"flag_id": flag["id"], **({"idr_id": flag["claimed_by_idr_id"]} if with_idr else {}), **clip})
    return clips


def effective_frontage(annotation: Optional[dict[str, Any]], prop: dict[str, Any],
                       scale: Optional[float]) -> tuple[Optional[Decimal], str]:
    """
    The frontage a page's site is billed to: the page's sidewalk strip (its first edge), else the page's tax-map width,
    else the site's MapPLUTO frontage, else none.
    Takes the page's title block (sidewalk_strip_points, tax_map_width_ft, tax_map_source; None for none), the property
    (tax_map_frontage_ft, tax_map_source) and the page's px/ft.
    Returns (feet to the cent, or None; what it came from: 'sidewalk_strip', the width's or the site's tax_map_source,
    or 'unavailable').
    """
    annotation = annotation or {}
    if annotation.get("sidewalk_strip_points"):
        frontage = strip_frontage_ft(strip_points(annotation["sidewalk_strip_points"]), scale)
        if frontage is not None:
            return frontage, STRIP_LABEL
    if annotation.get("tax_map_width_ft") is not None:
        return Decimal(annotation["tax_map_width_ft"]).quantize(CENT), annotation.get("tax_map_source") or "tax_map"
    if prop.get("tax_map_frontage_ft") is not None:
        return Decimal(prop["tax_map_frontage_ft"]).quantize(CENT), prop.get("tax_map_source") or "tax_map"
    return None, UNAVAILABLE
